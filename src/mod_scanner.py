"""
模组扫描模块
负责扫描JAR文件，提取英文语言文件和模组信息
"""

import os
import json
import zipfile
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass


@dataclass
class ModInfo:
    """模组信息数据类"""
    mod_id: str
    mod_name: str
    jar_path: str
    en_us_content: Dict[str, str]
    has_zh_cn: bool
    assets_path: str  # assets/mod_id路径


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
                mod_info = self._scan_single_mod(jar_file)
                if mod_info:
                    mod_infos.append(mod_info)
            except Exception as e:
                print(f"警告: 扫描 {jar_file.name} 时出错: {e}")
                continue

        return mod_infos

    def _scan_single_mod(self, jar_path: Path) -> Optional[ModInfo]:
        """
        扫描单个模组JAR文件

        Args:
            jar_path: JAR文件路径

        Returns:
            模组信息，如果不需要翻译则返回None
        """
        try:
            with zipfile.ZipFile(jar_path, 'r') as jar:
                # 查找所有可能的语言文件路径
                file_list = jar.namelist()

                # 查找 assets/*/lang/en_us.json
                en_us_files = [f for f in file_list if self._is_en_us_lang_file(f)]

                if not en_us_files:
                    # print(f"跳过 {jar_path.name}: 未找到英文语言文件")
                    return None

                # 使用第一个找到的英文语言文件
                en_us_path = en_us_files[0]

                # 提取mod_id (从路径 assets/mod_id/lang/en_us.json)
                mod_id = self._extract_mod_id(en_us_path)
                if not mod_id:
                    print(f"警告: 无法从 {en_us_path} 提取mod_id")
                    return None

                # 读取英文语言文件
                with jar.open(en_us_path) as f:
                    en_us_content = json.loads(f.read().decode('utf-8'))

                # 检查是否已有中文文件
                zh_cn_path = en_us_path.replace('en_us.json', 'zh_cn.json')
                has_zh_cn = zh_cn_path in file_list

                # 尝试获取模组名称
                mod_name = self._get_mod_name(jar, mod_id, en_us_content)

                return ModInfo(
                    mod_id=mod_id,
                    mod_name=mod_name,
                    jar_path=str(jar_path),
                    en_us_content=en_us_content,
                    has_zh_cn=has_zh_cn,
                    assets_path=f"assets/{mod_id}"
                )

        except zipfile.BadZipFile:
            print(f"警告: {jar_path.name} 不是有效的JAR文件")
            return None
        except json.JSONDecodeError:
            print(f"警告: {jar_path.name} 的语言文件JSON格式错误")
            return None
        except Exception as e:
            print(f"警告: 处理 {jar_path.name} 时出错: {e}")
            return None

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
        match = re.match(r'^assets/([^/]+)/lang/', lang_file_path)
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
        already_translated = sum(1 for mod in mod_infos if mod.has_zh_cn)
        need_translation = total_mods - already_translated
        total_keys = sum(len(mod.en_us_content) for mod in mod_infos)

        return {
            'total_mods': total_mods,
            'already_translated': already_translated,
            'need_translation': need_translation,
            'total_translation_keys': total_keys
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
    print(f"已有中文: {summary['already_translated']}")
    print(f"需要翻译: {summary['need_translation']}")
    print(f"总翻译条目: {summary['total_translation_keys']}")

    print("\n需要翻译的模组:")
    for mod in mod_infos:
        if not mod.has_zh_cn:
            print(f"  - {mod.mod_name} ({mod.mod_id}): {len(mod.en_us_content)} 条")


if __name__ == '__main__':
    test_scanner()
