"""
翻译器模块
支持 AI API（DeepSeek、OpenAI 兼容接口）和传统翻译 API。
"""

import hashlib
import json
import random
import re
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional

import requests


class TranslationCancelled(Exception):
    """用户主动取消翻译。"""


class _BatchTranslationError(Exception):
    """批量翻译响应不可用，可由调用方回退为逐条翻译。"""


class TranslationCache:
    """按翻译器配置隔离的持久化缓存。"""

    CACHE_VERSION = "v2"

    def __init__(self, cache_dir: str = "cache", namespace: str = "default"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "translation_cache.json"
        self.namespace = namespace
        self.cache: Dict[str, str] = self._load_cache()

    def _load_cache(self) -> Dict[str, str]:
        if not self.cache_file.exists():
            return {}

        try:
            with open(self.cache_file, "r", encoding="utf-8") as file:
                data = json.load(file)
                return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _namespace_prefix(self) -> str:
        namespace_hash = hashlib.sha256(self.namespace.encode("utf-8")).hexdigest()[:16]
        return f"{self.CACHE_VERSION}:{namespace_hash}"

    def _make_key(self, text: str) -> str:
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return f"{self._namespace_prefix()}:{text_hash}"

    def get(self, text: str) -> Optional[str]:
        return self.cache.get(self._make_key(text))

    def set(self, text: str, translation: str):
        self.cache[self._make_key(text)] = translation

    def save_cache(self):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with open(self.cache_file, "w", encoding="utf-8") as file:
            json.dump(self.cache, file, ensure_ascii=False, indent=2)

    def clear(self):
        """只清理当前翻译器命名空间的缓存。"""
        prefix = f"{self._namespace_prefix()}:"
        self.cache = {
            key: value
            for key, value in self.cache.items()
            if not key.startswith(prefix)
        }
        self.save_cache()

    @classmethod
    def clear_all(cls, cache_dir: str = "cache"):
        """清理所有命名空间的翻译缓存。"""
        cache_file = Path(cache_dir) / "translation_cache.json"
        if cache_file.exists():
            cache_file.unlink()


class BaseTranslator(ABC):
    """翻译器基类，负责去重、缓存、批处理和进度上报。"""

    def __init__(
        self,
        use_cache: bool = True,
        batch_size: int = 10,
        max_workers: int = 3,
        cache_namespace: str = "default",
    ):
        self.use_cache = use_cache
        self.batch_size = max(1, int(batch_size))
        self.max_workers = max(1, int(max_workers))
        self.mc_terminology: Dict[str, str] = {}
        self._cache_namespace = cache_namespace or self.__class__.__name__
        self.cache = (
            TranslationCache(namespace=self._build_namespace()) if use_cache else None
        )

    def _build_namespace(self) -> str:
        terminology = json.dumps(
            self.mc_terminology, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return f"{self._cache_namespace}|target=zh_cn|terms={terminology}"

    def set_terminology(self, terminology: Dict[str, str]):
        self.mc_terminology = terminology or {}
        if self.cache is not None:
            self.cache.namespace = self._build_namespace()

    @staticmethod
    def _raise_if_cancelled(cancel_check=None):
        if cancel_check and cancel_check():
            raise TranslationCancelled("翻译已取消")

    def translate_dict(
        self,
        content: Dict[str, str],
        progress_callback=None,
        cancel_check=None,
    ) -> Dict[str, str]:
        """
        翻译整个语言字典。

        相同原文只会请求一次；多个 key 会复用同一份译文。AI 翻译器会按
        batch_size 合并请求，并通过 max_workers 控制并发。
        """
        if not content:
            return {}

        result: Dict[str, str] = {}
        pending: Dict[str, List[str]] = {}
        total = len(content)
        inspected = 0

        for key, value in content.items():
            self._raise_if_cancelled(cancel_check)
            inspected += 1

            if not isinstance(value, str):
                result[key] = value
                continue
            if not value.strip() or self._is_chinese(value):
                result[key] = value
                if progress_callback:
                    reason = "空文本" if not value.strip() else "已是中文"
                    progress_callback(inspected, total, f"跳过({reason}): {key}")
                continue

            cached = self.cache.get(value) if self.cache is not None else None
            if cached is not None:
                result[key] = cached
                if progress_callback:
                    progress_callback(inspected, total, f"缓存命中: {key}")
                continue

            pending.setdefault(value, []).append(key)

        pending_item_count = sum(len(keys) for keys in pending.values())
        skipped_count = total - pending_item_count
        unique_texts = list(pending.keys())
        batches = [
            unique_texts[index:index + self.batch_size]
            for index in range(0, len(unique_texts), self.batch_size)
        ]

        completed_items = 0

        def store_batch(
            sources: List[str], translations: List[Optional[str]]
        ):
            nonlocal completed_items
            if len(sources) != len(translations):
                raise RuntimeError("批量翻译结果数量与原文数量不一致")

            for source, translated in zip(sources, translations):
                valid_translation = (
                    isinstance(translated, str) and bool(translated.strip())
                )
                final_text = source
                if valid_translation:
                    final_text = self._apply_terminology(translated)
                    valid_translation = bool(final_text.strip())

                for key in pending[source]:
                    result[key] = final_text
                if valid_translation and self.cache is not None:
                    self.cache.set(source, final_text)

                completed_items += len(pending[source])

            if progress_callback:
                current = min(total, skipped_count + completed_items)
                progress_callback(current, total, f"已批量翻译 {completed_items} 项")

        try:
            if batches:
                worker_count = min(self.max_workers, len(batches))
                if worker_count == 1:
                    for batch in batches:
                        self._raise_if_cancelled(cancel_check)
                        translations = self._translate_batch_safely(batch)
                        store_batch(batch, translations)
                else:
                    executor = ThreadPoolExecutor(max_workers=worker_count)
                    future_map = {
                        executor.submit(self._translate_batch_safely, batch): batch
                        for batch in batches
                    }
                    try:
                        for future in as_completed(future_map):
                            self._raise_if_cancelled(cancel_check)
                            batch = future_map[future]
                            translations = future.result()
                            store_batch(batch, translations)
                    finally:
                        for future in future_map:
                            future.cancel()
                        executor.shutdown(wait=True)
        except TranslationCancelled:
            raise
        finally:
            if self.cache is not None:
                self.cache.save_cache()

        return result

    def _translate_batch_safely(
        self, texts: List[str]
    ) -> List[Optional[str]]:
        """返回批次译文；失败项用 None 标记且不会写入缓存。"""
        try:
            translations = self.translate_batch(texts)
            if len(translations) != len(texts):
                raise _BatchTranslationError("批量翻译结果数量不一致")
            return [
                translated
                if isinstance(translated, str) and translated.strip()
                else None
                for translated in translations
            ]
        except TranslationCancelled:
            raise
        except Exception as error:
            print(f"批量翻译失败，已保留原文: {error}")
            return [None] * len(texts)

    def translate_batch(self, texts: List[str]) -> List[str]:
        """默认逐条调用 translate；支持批量的服务可覆盖此方法。"""
        translations = []
        for text in texts:
            translations.append(self.translate(text))
            time.sleep(0.05)
        return translations

    def _is_chinese(self, text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", text))

    def _apply_terminology(self, text: str) -> str:
        for english, chinese in self.mc_terminology.items():
            pattern = r"\b" + re.escape(english) + r"\b"
            text = re.sub(pattern, chinese, text, flags=re.IGNORECASE)
        return text

    @abstractmethod
    def translate(self, text: str) -> str:
        """翻译单条文本。"""


class AITranslator(BaseTranslator):
    """OpenAI Chat Completions 兼容翻译器，支持批量 JSON 翻译。"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
        temperature: float = 0.3,
        max_tokens: int = 2000,
        use_cache: bool = True,
        batch_size: int = 10,
        max_workers: int = 3,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        namespace = f"ai|base={self.base_url}|model={self.model}|temperature={temperature}"
        super().__init__(
            use_cache=use_cache,
            batch_size=batch_size,
            max_workers=max_workers,
            cache_namespace=namespace,
        )

    def translate(self, text: str) -> str:
        prompt = self._build_single_prompt(text)
        translated = self._request(prompt)
        return self._clean_translation(translated)

    def translate_batch(self, texts: List[str]) -> List[str]:
        if len(texts) == 1:
            return [self.translate(texts[0])]

        payload = {f"t{index + 1}": text for index, text in enumerate(texts)}
        prompt = self._build_batch_prompt(payload)

        try:
            raw_response = self._request(prompt)
            return self._parse_batch_response(raw_response, len(texts))
        except _BatchTranslationError as error:
            print(f"AI 批量响应解析失败，回退逐条翻译: {error}")
            return [self.translate(text) for text in texts]

    def _request(self, prompt: str) -> str:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        data = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=data,
                timeout=60,
            )
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"].strip()
        except requests.exceptions.RequestException as error:
            raise RuntimeError(f"AI翻译请求失败: {error}") from error
        except (KeyError, IndexError, TypeError) as error:
            raise RuntimeError(f"AI翻译响应解析失败: {error}") from error

    @staticmethod
    def _build_single_prompt(text: str) -> str:
        return f"""请将以下 Minecraft 模组英文文本翻译成简体中文。
要求：
1. 保持游戏术语准确，语言简洁自然。
2. 保留 %s、%d、{{0}}、\\n、§ 等格式占位符。
3. 只返回译文，不要解释，不要添加引号。

原文：{text}

中文翻译："""

    @staticmethod
    def _build_batch_prompt(payload: Dict[str, str]) -> str:
        source_json = json.dumps(payload, ensure_ascii=False, indent=2)
        return f"""你是 Minecraft 模组本地化译者。请把 INPUT_JSON 中每个 value 从英文翻译成简体中文。

必须遵守：
1. 返回一个合法 JSON 对象，key 必须与输入完全一致。
2. 只返回 JSON，不要 Markdown 代码块或解释。
3. 保留 %s、%d、{{0}}、\\n、§、颜色代码等格式标记。
4. 命令、URL、注册名和专有名词没有公认译名时保持原文。
5. 译文要简洁，适合游戏界面显示。

INPUT_JSON：
{source_json}

OUTPUT_JSON："""

    def _parse_batch_response(self, response_text: str, expected_count: int) -> List[str]:
        cleaned = response_text.strip()
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s*```$', '', cleaned)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as error:
            raise _BatchTranslationError(f"返回内容不是有效 JSON: {error}") from error

        result: List[str] = []
        if isinstance(data, dict):
            for index in range(1, expected_count + 1):
                value = data.get(f"t{index}", data.get(str(index)))
                if not isinstance(value, str):
                    raise _BatchTranslationError(f"缺少译文 key: t{index}")
                result.append(self._clean_translation(value))
            return result

        if isinstance(data, list) and len(data) == expected_count:
            if not all(isinstance(item, str) for item in data):
                raise _BatchTranslationError("JSON 数组包含非字符串内容")
            return [self._clean_translation(item) for item in data]

        raise _BatchTranslationError("返回 JSON 结构不符合要求")

    @staticmethod
    def _clean_translation(text: str) -> str:
        text = text.strip().strip('"\'“”‘’')
        text = re.sub(r"^(中文翻译?：|翻译：|译文：)\s*", "", text)
        return text.strip()


class BaiduTranslator(BaseTranslator):
    """百度翻译 API。"""

    def __init__(
        self,
        app_id: str,
        secret_key: str,
        use_cache: bool = True,
        batch_size: int = 1,
        max_workers: int = 3,
    ):
        self.app_id = app_id
        self.secret_key = secret_key
        self.api_url = "https://fanyi-api.baidu.com/api/trans/vip/translate"
        super().__init__(
            use_cache=use_cache,
            batch_size=batch_size,
            max_workers=max_workers,
            cache_namespace=f"baidu|app_id={app_id}",
        )

    def translate(self, text: str) -> str:
        salt = random.randint(32768, 65536)
        sign_source = f"{self.app_id}{text}{salt}{self.secret_key}"
        sign = hashlib.md5(sign_source.encode("utf-8")).hexdigest()
        params = {
            "q": text,
            "from": "en",
            "to": "zh",
            "appid": self.app_id,
            "salt": salt,
            "sign": sign,
        }

        try:
            response = requests.get(self.api_url, params=params, timeout=15)
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.RequestException as error:
            raise RuntimeError(f"百度翻译请求失败: {error}") from error

        if "trans_result" in result and result["trans_result"]:
            return result["trans_result"][0]["dst"]
        if "error_code" in result:
            raise RuntimeError(
                f"百度翻译错误: {result.get('error_msg', 'Unknown error')}"
            )
        raise RuntimeError("百度翻译返回格式错误")


class TencentTranslator(BaseTranslator):
    """腾讯翻译 API 占位实现。"""

    def __init__(self, secret_id: str, secret_key: str, use_cache: bool = True):
        self.secret_id = secret_id
        self.secret_key = secret_key
        super().__init__(use_cache=use_cache, cache_namespace=f"tencent|id={secret_id}")

    def translate(self, text: str) -> str:
        raise NotImplementedError("腾讯翻译 API 需要完整的腾讯云签名实现")


class DeepLTranslator(BaseTranslator):
    """DeepL 翻译 API，使用原生多文本批量接口。"""

    def __init__(
        self,
        auth_key: str,
        use_cache: bool = True,
        batch_size: int = 10,
        max_workers: int = 3,
    ):
        self.auth_key = auth_key
        self.api_url = "https://api-free.deepl.com/v2/translate"
        super().__init__(
            use_cache=use_cache,
            batch_size=batch_size,
            max_workers=max_workers,
            cache_namespace=f"deepl|auth={auth_key}",
        )

    def translate(self, text: str) -> str:
        return self.translate_batch([text])[0]

    def translate_batch(self, texts: List[str]) -> List[str]:
        headers = {
            "Authorization": f"DeepL-Auth-Key {self.auth_key}",
            "Content-Type": "application/json",
        }
        data = {"text": texts, "target_lang": "ZH", "source_lang": "EN"}

        try:
            response = requests.post(
                self.api_url, headers=headers, json=data, timeout=30
            )
            response.raise_for_status()
            result = response.json()
            translations = result.get("translations", [])
            if len(translations) != len(texts):
                raise _BatchTranslationError("DeepL 返回数量不一致")
            return [item["text"] for item in translations]
        except requests.exceptions.RequestException as error:
            raise RuntimeError(f"DeepL 翻译请求失败: {error}") from error
        except (KeyError, TypeError) as error:
            raise RuntimeError(f"DeepL 翻译返回格式错误: {error}") from error


class TranslatorFactory:
    """根据配置创建翻译器。"""

    @staticmethod
    def create_translator(config: Dict) -> BaseTranslator:
        api_type = config.get("api_type", "ai")
        api_configs = config.get("api_configs", {})
        settings = config.get("translation_settings", {})
        use_cache = settings.get("use_cache", True)
        batch_size = int(settings.get("batch_size", 10) or 10)
        max_workers = int(settings.get("max_workers", 3) or 3)

        if api_type == "ai":
            ai_config = api_configs.get("ai", {})
            translator = AITranslator(
                api_key=ai_config.get("api_key", ""),
                base_url=ai_config.get("base_url", "https://api.deepseek.com/v1"),
                model=ai_config.get("model", "deepseek-chat"),
                temperature=ai_config.get("temperature", 0.3),
                max_tokens=ai_config.get("max_tokens", 2000),
                use_cache=use_cache,
                batch_size=batch_size,
                max_workers=max_workers,
            )
        elif api_type == "baidu":
            baidu_config = api_configs.get("baidu", {})
            translator = BaiduTranslator(
                app_id=baidu_config.get("app_id", ""),
                secret_key=baidu_config.get("secret_key", ""),
                use_cache=use_cache,
                max_workers=max_workers,
            )
        elif api_type == "deepl":
            deepl_config = api_configs.get("deepl", {})
            translator = DeepLTranslator(
                auth_key=deepl_config.get("auth_key", ""),
                use_cache=use_cache,
                batch_size=batch_size,
                max_workers=max_workers,
            )
        elif api_type == "tencent":
            tencent_config = api_configs.get("tencent", {})
            translator = TencentTranslator(
                secret_id=tencent_config.get("secret_id", ""),
                secret_key=tencent_config.get("secret_key", ""),
                use_cache=use_cache,
            )
        else:
            raise ValueError(f"不支持的 API 类型: {api_type}")

        translator.set_terminology(settings.get("mc_terminology", {}))
        return translator


def test_translator():
    """命令行翻译测试。"""
    test_texts = {
        "item.example.sword": "Enchanted Diamond Sword",
        "block.example.ore": "Mysterious Ore Block",
        "tooltip.example.info": "This item has %s durability",
    }

    print("请输入 DeepSeek API Key 进行测试：")
    api_key = input().strip()
    if not api_key:
        print("未提供 API Key，跳过测试")
        return

    translator = AITranslator(api_key=api_key, use_cache=True)
    result = translator.translate_dict(
        test_texts,
        progress_callback=lambda current, total, message: print(
            f"[{current}/{total}] {message}"
        ),
    )
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    test_translator()
