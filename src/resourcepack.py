"""
资源包生成模块
负责将翻译后的语言文件组织成 Minecraft 资源包并打包。
"""

import json
import os
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Mapping, Union

from mod_scanner import ModInfo


class ResourcePackGenerator:
    """Minecraft 资源包生成器。"""

    def __init__(
        self,
        pack_format: int = 15,
        description: str = "自动生成的中文汉化补丁",
    ):
        self.pack_format = pack_format
        self.description = description

    def generate(
        self,
        mod_translations: Mapping[Union[str, ModInfo], Dict[str, str]],
        output_path: str,
        cancel_check=None,
        additional_files: Mapping[str, Union[str, bytes]] = None,
    ) -> str:
        """生成资源包，并通过临时文件原子替换最终 ZIP。"""
        if cancel_check and cancel_check():
            from translator import TranslationCancelled

            raise TranslationCancelled("资源包生成已取消")

        final_path = self._resolve_output_path(output_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_zip = final_path.with_name(
            f".{final_path.name}.{os.getpid()}.tmp"
        )

        try:
            with tempfile.TemporaryDirectory(prefix="mc_hanhua_pack_") as temp:
                temp_dir = Path(temp)
                self._create_pack_meta(temp_dir)
                assets_dir = temp_dir / "assets"
                assets_dir.mkdir(parents=True, exist_ok=True)

                for mod_key, translations in mod_translations.items():
                    if cancel_check and cancel_check():
                        from translator import TranslationCancelled

                        raise TranslationCancelled("资源包生成已取消")
                    mod_id = (
                        mod_key.mod_id
                        if isinstance(mod_key, ModInfo)
                        else str(mod_key)
                    )
                    self._create_mod_lang_file(assets_dir, mod_id, translations)

                if additional_files:
                    self._create_additional_files(temp_dir, additional_files)

                self._create_zip(temp_dir, temporary_zip)

            os.replace(str(temporary_zip), str(final_path))
            return str(final_path)
        finally:
            if temporary_zip.exists():
                temporary_zip.unlink()

    def _resolve_output_path(self, output_path: str) -> Path:
        path = Path(output_path)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if (path.exists() and path.is_dir()) or not path.suffix:
            return path / f"汉化补丁_{timestamp}.zip"
        return path

    def _create_pack_meta(self, pack_dir: Path):
        pack_meta = {
            "pack": {
                "pack_format": self.pack_format,
                "description": self.description,
            }
        }
        meta_file = pack_dir / "pack.mcmeta"
        with open(meta_file, "w", encoding="utf-8") as file:
            json.dump(pack_meta, file, ensure_ascii=False, indent=2)

    def _create_mod_lang_file(
        self,
        assets_dir: Path,
        mod_id: str,
        translations: Dict[str, str],
    ):
        mod_lang_dir = assets_dir / mod_id / "lang"
        mod_lang_dir.mkdir(parents=True, exist_ok=True)
        lang_file = mod_lang_dir / "zh_cn.json"
        with open(lang_file, "w", encoding="utf-8") as file:
            json.dump(translations, file, ensure_ascii=False, indent=2)

    @staticmethod
    def _create_additional_files(
        pack_dir: Path,
        files: Mapping[str, Union[str, bytes]],
    ):
        base = pack_dir.resolve()
        for relative_name, content in files.items():
            relative_path = Path(relative_name)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise ValueError(f"资源包附加文件路径不安全: {relative_name}")

            target = (pack_dir / relative_path).resolve()
            if target != base and base not in target.parents:
                raise ValueError(f"资源包附加文件路径越界: {relative_name}")

            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding="utf-8")

    @staticmethod
    def _create_zip(source_dir: Path, output_path: Path):
        with zipfile.ZipFile(
            output_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for file_path in sorted(source_dir.rglob("*")):
                if file_path.is_file():
                    archive.write(file_path, file_path.relative_to(source_dir))

    def verify_resourcepack(self, zip_path: str) -> bool:
        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                names = archive.namelist()
                if "pack.mcmeta" not in names:
                    return False
                return any(
                    name.endswith("lang/zh_cn.json") for name in names
                )
        except (OSError, zipfile.BadZipFile):
            return False

    def get_resourcepack_info(self, zip_path: str) -> Dict:
        info = {
            "valid": False,
            "pack_format": None,
            "description": None,
            "mod_count": 0,
            "total_translations": 0,
            "mods": [],
        }

        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                names = archive.namelist()
                if "pack.mcmeta" in names:
                    with archive.open("pack.mcmeta") as file:
                        pack_meta = json.load(file)
                        pack = pack_meta.get("pack", {})
                        info["pack_format"] = pack.get("pack_format")
                        info["description"] = pack.get("description")

                lang_files = [
                    name for name in names if name.endswith("lang/zh_cn.json")
                ]
                info["mod_count"] = len(lang_files)

                for lang_file in lang_files:
                    parts = lang_file.split("/")
                    if len(parts) < 3 or parts[0] != "assets":
                        continue
                    mod_id = parts[1]
                    with archive.open(lang_file) as file:
                        translations = json.load(file)
                    count = len(translations) if isinstance(translations, dict) else 0
                    info["total_translations"] += count
                    info["mods"].append(
                        {"mod_id": mod_id, "translation_count": count}
                    )

                info["valid"] = True
        except (OSError, zipfile.BadZipFile, json.JSONDecodeError, KeyError) as error:
            print(f"读取资源包信息失败: {error}")

        return info


class ResourcePackUpdater:
    """在已有资源包基础上增量更新译文。"""

    def __init__(self, existing_pack: str):
        self.existing_pack = Path(existing_pack)
        if not self.existing_pack.exists():
            raise FileNotFoundError(f"资源包不存在: {existing_pack}")

    def add_translations(
        self,
        mod_translations: Mapping[Union[str, ModInfo], Dict[str, str]],
        output_path: str,
    ) -> str:
        generator = ResourcePackGenerator()
        final_path = generator._resolve_output_path(output_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_zip = final_path.with_name(
            f".{final_path.name}.{os.getpid()}.tmp"
        )

        try:
            with tempfile.TemporaryDirectory(prefix="mc_hanhua_update_") as temp:
                temp_dir = Path(temp)
                self._safe_extract(temp_dir)
                assets_dir = temp_dir / "assets"
                assets_dir.mkdir(parents=True, exist_ok=True)

                for mod_key, translations in mod_translations.items():
                    mod_id = (
                        mod_key.mod_id
                        if isinstance(mod_key, ModInfo)
                        else str(mod_key)
                    )
                    lang_file = assets_dir / mod_id / "lang" / "zh_cn.json"
                    merged_translations = dict(translations)
                    if lang_file.exists():
                        with open(lang_file, "r", encoding="utf-8") as file:
                            existing_translations = json.load(file)
                        if not isinstance(existing_translations, dict):
                            raise ValueError(
                                f"???????? JSON ??: {mod_id}"
                            )
                        existing_translations.update(merged_translations)
                        merged_translations = existing_translations

                    generator._create_mod_lang_file(
                        assets_dir, mod_id, merged_translations
                    )

                generator._create_zip(temp_dir, temporary_zip)

            os.replace(str(temporary_zip), str(final_path))
            return str(final_path)
        finally:
            if temporary_zip.exists():
                temporary_zip.unlink()

    def _safe_extract(self, destination: Path):
        base = destination.resolve()
        with zipfile.ZipFile(self.existing_pack, "r") as archive:
            for member in archive.infolist():
                target = (destination / member.filename).resolve()
                if target != base and base not in target.parents:
                    raise ValueError(f"资源包包含不安全路径: {member.filename}")
            archive.extractall(destination)


def test_generator():
    test_mod = ModInfo(
        mod_id="example_mod",
        mod_name="Example Mod",
        jar_path="test.jar",
        en_us_content={"item.example.sword": "Diamond Sword"},
        has_zh_cn=False,
        assets_path="assets/example_mod",
    )
    translations = {
        test_mod.mod_id: {
            "item.example.sword": "钻石剑",
            "block.example.ore": "矿石方块",
            "tooltip.example.info": "这是一个测试物品",
        }
    }

    output_path = "test_resourcepack.zip"
    generator = ResourcePackGenerator(pack_format=15, description="测试汉化补丁")
    result_path = generator.generate(translations, output_path)
    print(f"资源包已生成: {result_path}")

    if generator.verify_resourcepack(result_path):
        print("资源包验证通过")
        print(generator.get_resourcepack_info(result_path))
    else:
        print("资源包验证失败")


if __name__ == "__main__":
    test_generator()
