"""
模组扫描模块
负责扫描JAR文件，提取英文语言文件和模组信息
"""

import os
import json
import zipfile
import re
from pathlib import Path, PurePosixPath
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class ModInfo:
    """模组信息数据类"""
    mod_id: str
    mod_name: str
    jar_path: str
    en_us_content: Dict[str, str]
    has_zh_cn: bool
    assets_path: str  # assets/mod_id路径
    zh_cn_content: Dict[str, str] = field(default_factory=dict)
    missing_translation_keys: List[str] = field(default_factory=list)

    @property
    def is_fully_translated(self) -> bool:
        """是否已经覆盖全部英文语言键且没有空译文。"""
        return self.has_zh_cn and not self.missing_translation_keys

    @property
    def pending_translation_count(self) -> int:
        """当前模组需要翻译或补全的条目数。"""
        return len(self.missing_translation_keys)

    def get_pending_en_us_content(self) -> Dict[str, str]:
        """返回缺失或空译文对应的英文源文本。"""
        return {
            key: self.en_us_content[key]
            for key in self.missing_translation_keys
            if key in self.en_us_content
        }

    def merge_with_existing(self, translations: Dict[str, str]) -> Dict[str, str]:
        """保留已有译文和额外键，并合并本次生成结果。"""
        merged = dict(self.zh_cn_content)
        merged.update(translations)
        return merged


class ModScanner:
    """模组扫描器"""

    def __init__(self, mods_folder: str):
        """
        初始化扫描器

        Args:
            mods_folder: mods文件夹路径
        """
        self.mods_folder = Path(mods_folder)
        if not self.mods_folder.exists():
            raise ValueError(f"模组文件夹不存在: {mods_folder}")

    def scan_all_mods(self) -> List[ModInfo]:
        """
        扫描所有模组

        Returns:
            模组信息列表
        """
        jar_files = list(self.mods_folder.glob("*.jar"))
        mod_infos = []

        for jar_file in jar_files:
            try:
                mod_infos.extend(self._scan_single_mod(jar_file))
            except Exception as e:
                print(f"警告: 扫描 {jar_file.name} 时出错: {e}")
                continue

        return mod_infos

    def _scan_single_mod(self, jar_path: Path) -> List[ModInfo]:
        """
        扫描单个模组JAR文件中的全部语言命名空间。

        Args:
            jar_path: JAR文件路径

        Returns:
            模组信息列表；没有可处理的语言文件时返回空列表
        """
        mod_infos: List[ModInfo] = []

        try:
            with zipfile.ZipFile(jar_path, 'r') as jar:
                file_list = jar.namelist()
                en_us_files = [f for f in file_list if self._is_en_us_lang_file(f)]

                for en_us_path in en_us_files:
                    try:
                        mod_id = self._extract_mod_id(en_us_path)
                        if not mod_id:
                            print(f"警告: 无法从 {en_us_path} 提取mod_id")
                            continue

                        with jar.open(en_us_path) as f:
                            en_us_content = json.loads(f.read().decode('utf-8'))

                        if not isinstance(en_us_content, dict):
                            raise ValueError("英文语言文件顶层必须是 JSON 对象")

                        zh_cn_path = self._find_zh_cn_path(
                            en_us_path, file_list
                        )
                        has_zh_cn = zh_cn_path is not None
                        zh_cn_content = self._load_zh_cn_content(
                            jar, zh_cn_path
                        )
                        missing_keys = self._find_missing_translation_keys(
                            en_us_content, zh_cn_content
                        )
                        mod_name = self._get_mod_name(
                            jar, mod_id, en_us_content
                        )

                        mod_infos.append(ModInfo(
                            mod_id=mod_id,
                            mod_name=mod_name,
                            jar_path=str(jar_path),
                            en_us_content=en_us_content,
                            has_zh_cn=has_zh_cn,
                            assets_path=f"assets/{mod_id}",
                            zh_cn_content=zh_cn_content,
                            missing_translation_keys=missing_keys,
                        ))
                    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
                        print(
                            f"警告: {jar_path.name} 中的 {en_us_path} "
                            f"无法处理: {e}"
                        )
                    except Exception as e:
                        print(
                            f"警告: 处理 {jar_path.name} 中的 {en_us_path} "
                            f"时出错: {e}"
                        )

        except zipfile.BadZipFile:
            print(f"警告: {jar_path.name} 不是有效的JAR文件")
        except Exception as e:
            print(f"警告: 处理 {jar_path.name} 时出错: {e}")

        return mod_infos

    @staticmethod
    def _find_zh_cn_path(
        en_us_path: str, file_list: List[str]
    ) -> Optional[str]:
        """查找与英文文件同目录的 zh_cn.json，并兼容文件名大小写。"""
        expected = str(PurePosixPath(en_us_path).with_name('zh_cn.json'))
        if expected in file_list:
            return expected

        expected_lower = expected.lower()
        return next(
            (path for path in file_list if path.lower() == expected_lower),
            None,
        )

    @staticmethod
    def _load_zh_cn_content(
        jar: zipfile.ZipFile, zh_cn_path: Optional[str]
    ) -> Dict[str, str]:
        """读取中文语言文件；损坏或非对象内容按缺失中文处理。"""
        if not zh_cn_path:
            return {}

        try:
            with jar.open(zh_cn_path) as f:
                content = json.loads(f.read().decode('utf-8'))
            if not isinstance(content, dict):
                raise ValueError("中文语言文件顶层必须是 JSON 对象")
            return content
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
            print(f"警告: {zh_cn_path} 无法读取，将按缺失全部中文处理: {e}")
            return {}

    @staticmethod
    def _find_missing_translation_keys(
        en_us_content: Dict[str, str],
        zh_cn_content: Dict[str, str],
    ) -> List[str]:
        """返回中文文件缺失或值为空的英文语言键。"""
        missing_keys = []
        for key in en_us_content:
            translated = zh_cn_content.get(key)
            if not isinstance(translated, str) or not translated.strip():
                missing_keys.append(key)
        return missing_keys

    def _is_en_us_lang_file(self, file_path: str) -> bool:
        """
        判断是否是英文语言文件

        Args:
            file_path: 文件路径

        Returns:
            是否是英文语言文件
        """
        # 标准路径: assets/mod_id/lang/en_us.json
        pattern = r'^assets/[^/]+/lang/en_us\.json$'
        return bool(re.match(pattern, file_path.lower()))

    def _extract_mod_id(self, lang_file_path: str) -> Optional[str]:
        """
        从语言文件路径提取mod_id

        Args:
            lang_file_path: 语言文件路径，如 assets/example_mod/lang/en_us.json

        Returns:
            mod_id
        """
        match = re.match(r'^assets/([^/]+)/lang/', lang_file_path, re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    def _get_mod_name(self, jar: zipfile.ZipFile, mod_id: str,
                      en_us_content: Dict[str, str]) -> str:
        """
        获取模组名称

        Args:
            jar: JAR文件对象
            mod_id: 模组ID
            en_us_content: 英文语言文件内容

        Returns:
            模组名称
        """
        # 尝试从语言文件中找模组名称
        possible_keys = [
            f"modmenu.mod.{mod_id}",
            f"mod.{mod_id}",
            f"{mod_id}.name",
            "item_group.name",
        ]

        for key in possible_keys:
            if key in en_us_content:
                return en_us_content[key]

        # 尝试从 mods.toml 或 fabric.mod.json 读取
        try:
            # Forge模组: META-INF/mods.toml
            if 'META-INF/mods.toml' in jar.namelist():
                with jar.open('META-INF/mods.toml') as f:
                    content = f.read().decode('utf-8')
                    match = re.search(r'displayName\s*=\s*"([^"]+)"', content)
                    if match:
                        return match.group(1)

            # Fabric模组: fabric.mod.json
            if 'fabric.mod.json' in jar.namelist():
                with jar.open('fabric.mod.json') as f:
                    fabric_meta = json.loads(f.read().decode('utf-8'))
                    if 'name' in fabric_meta:
                        return fabric_meta['name']
        except:
            pass

        # 默认返回mod_id
        return mod_id

    def get_summary(self, mod_infos: List[ModInfo]) -> Dict:
        """
        获取扫描摘要

        Args:
            mod_infos: 模组信息列表

        Returns:
            摘要信息字典
        """
        total_mods = len(mod_infos)
        has_zh_cn = sum(1 for mod in mod_infos if mod.has_zh_cn)
        fully_translated = sum(
            1 for mod in mod_infos if mod.is_fully_translated
        )
        partially_translated = sum(
            1
            for mod in mod_infos
            if mod.has_zh_cn and not mod.is_fully_translated
        )
        without_zh_cn = total_mods - has_zh_cn
        need_translation = total_mods - fully_translated
        total_keys = sum(len(mod.en_us_content) for mod in mod_infos)
        pending_keys = sum(
            mod.pending_translation_count for mod in mod_infos
        )
        partial_pending_keys = sum(
            mod.pending_translation_count
            for mod in mod_infos
            if mod.has_zh_cn and not mod.is_fully_translated
        )

        return {
            'total_mods': total_mods,
            'has_zh_cn': has_zh_cn,
            'already_translated': fully_translated,
            'fully_translated': fully_translated,
            'partially_translated': partially_translated,
            'without_zh_cn': without_zh_cn,
            'need_translation': need_translation,
            'total_translation_keys': total_keys,
            'pending_translation_keys': pending_keys,
            'partial_pending_translation_keys': partial_pending_keys,
        }


def test_scanner():
    """测试扫描器功能"""
    import sys

    if len(sys.argv) < 2:
        print("用法: python mod_scanner.py <mods文件夹路径>")
        return

    mods_folder = sys.argv[1]
    scanner = ModScanner(mods_folder)

    print(f"正在扫描 {mods_folder} ...")
    mod_infos = scanner.scan_all_mods()

    summary = scanner.get_summary(mod_infos)
    print(f"\n扫描完成!")
    print(f"总模组数: {summary['total_mods']}")
    print(f"已有中文文件: {summary['has_zh_cn']}")
    print(f"完整汉化: {summary['fully_translated']}")
    print(
        f"部分汉化: {summary['partially_translated']}，"
        f"缺少 {summary['partial_pending_translation_keys']} 条"
    )
    print(f"完全无中文: {summary['without_zh_cn']}")
    print(f"需要处理: {summary['need_translation']}")
    print(f"总翻译条目: {summary['total_translation_keys']}")

    print("\n需要处理的模组:")
    for mod in mod_infos:
        if not mod.is_fully_translated:
            print(
                f"  - {mod.mod_name} ({mod.mod_id}): "
                f"缺少或为空 {mod.pending_translation_count} 条"
            )

if __name__ == '__main__':
    test_scanner()
