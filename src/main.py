"""
MC模组汉化工具主程序
支持GUI模式和命令行模式
"""

import sys
import argparse
import json
from pathlib import Path

APP_VERSION = "1.2.0"

# 确保src目录在sys.path中
if hasattr(sys, '_MEIPASS'):
    # PyInstaller运行时
    import os
    src_path = os.path.join(sys._MEIPASS, 'src')
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    # 同时添加_MEIPASS本身
    if sys._MEIPASS not in sys.path:
        sys.path.insert(0, sys._MEIPASS)
else:
    # 正常Python运行时
    src_dir = Path(__file__).parent
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

try:
    from mod_scanner import ModScanner
    from translator import TranslatorFactory
    from resourcepack import ResourcePackGenerator
    from ftbquests import (
        FTBQuestsFolderGenerator,
        FTBQuestsImporter,
        FTBQuestsScanner,
        build_ftb_output_path,
        build_resourcepack_files,
        detect_ftbquests_directory,
        get_ftb_mode_label,
    )
except ImportError:
    # 尝试从src包导入
    from src.mod_scanner import ModScanner
    from src.translator import TranslatorFactory
    from src.resourcepack import ResourcePackGenerator
    from src.ftbquests import (
        FTBQuestsFolderGenerator,
        FTBQuestsImporter,
        FTBQuestsScanner,
        build_ftb_output_path,
        build_resourcepack_files,
        detect_ftbquests_directory,
        get_ftb_mode_label,
    )


def main_cli(args):
    """命令行模式主函数"""
    print(f"=== MC模组汉化工具 v{APP_VERSION} ===\n")

    try:
        # 1. 验证输入
        mods_path = Path(args.mods)
        if not mods_path.exists():
            print(f"错误: Mods文件夹不存在: {args.mods}")
            return 1

        # 2. 加载配置
        config = load_config(args.config)

        # 命令行参数覆盖配置文件
        api_configs = config.setdefault('api_configs', {})
        settings = config.setdefault('translation_settings', {})
        if args.api_type:
            config['api_type'] = args.api_type
        if args.api_key:
            if config['api_type'] == 'ai':
                api_configs.setdefault('ai', {})['api_key'] = args.api_key
        if args.batch_size is not None:
            settings['batch_size'] = max(1, args.batch_size)
        if args.max_workers is not None:
            settings['max_workers'] = max(1, args.max_workers)

        # 3. 扫描模组
        print(f"正在扫描模组: {args.mods}")
        scanner = ModScanner(args.mods)
        mod_infos = scanner.scan_all_mods()

        if mod_infos:
            summary = scanner.get_summary(mod_infos)
            print("\n模组扫描完成:")
            print(f"  总模组数: {summary['total_mods']}")
            print(f"  已有中文: {summary['already_translated']}")
            print(f"  需要翻译: {summary['need_translation']}")
            print(f"  总翻译条目: {summary['total_translation_keys']}")
        else:
            print("未找到包含英文语言文件的模组")

        # 3.1 扫描 FTB Quests 任务书
        ftb_info = None
        if not args.no_ftb_quests:
            ftb_path = args.ftb_quests
            if not ftb_path:
                detected = detect_ftbquests_directory(args.mods)
                ftb_path = str(detected) if detected else None

            if ftb_path:
                print(f"\n正在扫描 FTB Quests: {ftb_path}")
                ftb_info = FTBQuestsScanner(ftb_path).scan()
                for warning in ftb_info.warnings:
                    print(f"  警告: {warning}")
                print(f"  检测模式: {get_ftb_mode_label(ftb_info.mode)}")
                print(f"  可翻译条目: {ftb_info.translatable_count}")
            else:
                print("\n未检测到 FTB Quests 任务书目录，将仅处理模组文本")

        # 过滤已有中文的模组
        if not args.force:
            mod_infos = [m for m in mod_infos if not m.has_zh_cn]
            if mod_infos:
                print(f"\n跳过已有中文的模组，剩余 {len(mod_infos)} 个")

        ftb_entry_count = (
            ftb_info.translatable_count if ftb_info is not None else 0
        )
        if not mod_infos and not ftb_entry_count:
            print("没有需要翻译的文本")
            return 0

        # 4. 创建翻译器
        print("\n正在初始化翻译器...")
        translator = TranslatorFactory.create_translator(config)

        # 5. 翻译每个模组
        mod_translations = {}
        total_mods = len(mod_infos)
        total_jobs = int(bool(mod_infos)) + int(bool(ftb_entry_count))
        completed_jobs = 0

        for idx, mod_info in enumerate(mod_infos, 1):
            print(
                f"\n[{idx}/{total_mods}] 正在翻译: "
                f"{mod_info.mod_name} ({mod_info.mod_id})"
            )
            print(f"  翻译条目: {len(mod_info.en_us_content)} 条")

            def progress_callback(current, total, message, job_index=idx - 1):
                ratio = current / total if total else 1
                overall = ((job_index + ratio) / total_jobs) * 100
                print(f"\r  [{overall:5.1f}%] {message[:60]}", end="")

            try:
                translations = translator.translate_dict(
                    mod_info.en_us_content,
                    progress_callback=progress_callback,
                )
                print()
                mod_translations[mod_info.mod_id] = translations
                print("  [OK] 翻译完成")
            except KeyboardInterrupt:
                print("\n\n用户中断")
                return 1
            except Exception as error:
                print(f"  [ERROR] 翻译失败: {error}")
                if args.verbose:
                    import traceback
                    traceback.print_exc()
            completed_jobs += 1

        # 5.1 翻译 FTB Quests
        quest_translations = {}
        if ftb_info is not None and ftb_entry_count:
            print(f"\n正在翻译 FTB Quests 任务书: {ftb_entry_count} 条")

            def quest_progress(current, total, message):
                ratio = current / total if total else 1
                overall = ((completed_jobs + ratio) / total_jobs) * 100
                print(f"\r  [{overall:5.1f}%] {message[:60]}", end="")

            quest_translations = translator.translate_dict(
                ftb_info.translation_entries(),
                progress_callback=quest_progress,
            )
            print()
            completed_jobs += 1

        if not mod_translations and not quest_translations:
            print("\n没有成功翻译任何文本")
            return 1

        generated_any = False
        resourcepack_generated = False
        quest_folder_result = None

        ftb_resourcepack_files = {}
        if ftb_info is not None and quest_translations:
            ftb_resourcepack_files = build_resourcepack_files(
                ftb_info, quest_translations
            )

        # 6. 生成资源包。本地化键模式会直接合并到同一个资源包。
        if mod_translations or ftb_resourcepack_files:
            print("\n正在生成资源包...")
            generator = ResourcePackGenerator(
                pack_format=args.pack_format,
                description="自动生成的中文汉化补丁",
            )
            output_path = generator.generate(
                mod_translations,
                args.output,
                additional_files=ftb_resourcepack_files,
            )
            if generator.verify_resourcepack(output_path):
                info = generator.get_resourcepack_info(output_path)
                print(f"[OK] 资源包已生成: {output_path}")
                print(f"  包含命名空间: {info['mod_count']} 个")
                print(f"  翻译条目: {info['total_translations']} 条")
                resourcepack_generated = True
                generated_any = True
            else:
                print(f"[ERROR] 资源包验证失败: {output_path}")

        # 7. 内联式或外部语言文件模式需要完整替换 quests 目录。
        if (
            ftb_info is not None
            and ftb_info.requires_quests_folder
            and quest_translations
        ):
            ftb_output = args.ftb_output or build_ftb_output_path(args.output)
            print("\n正在生成完整 quests 汉化目录...")
            quest_folder_result = FTBQuestsFolderGenerator().generate(
                ftb_info, quest_translations, ftb_output
            )
            if quest_folder_result is not None:
                print(f"[OK] quests 目录已生成: {quest_folder_result.path}")
                print(f"  修改文件: {quest_folder_result.direct_count} 个")
                print(f"  语言文件条目: {quest_folder_result.lang_count} 条")
                generated_any = True

                if args.ftb_auto_import:
                    import_result = FTBQuestsImporter.import_quests(
                        quest_folder_result.path,
                        str(ftb_info.quests_dir),
                    )
                    print("[OK] 已自动替换当前整合包的 quests 目录")
                    print(f"  原目录备份: {import_result.backup_path}")
            else:
                print("FTB Quests 没有产生可写入的新译文")
        elif ftb_info is not None and ftb_info.mode == "localized_keys":
            print("FTB Quests 为本地化键模式，译文已合并到资源包，无需替换 quests。")

        if generated_any:
            print("\n=== 汉化完成 ===")
            if resourcepack_generated:
                print("资源包：请在 HMCL 等启动器中导入")
            if quest_folder_result is not None:
                if args.ftb_auto_import:
                    print("quests 目录：已自动备份并替换")
                else:
                    print(
                        "quests 目录：请手动备份原目录，再用生成目录替换 "
                        "config/ftbquests/quests"
                    )
            return 0

        print("\n没有生成任何补丁")
        return 1

    except Exception as e:
        print(f"\n发生错误: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


def main_gui():
    """GUI模式主函数"""
    try:
        from gui import run_gui
    except ImportError:
        from src.gui import run_gui
    run_gui()


def load_config(config_path: str) -> dict:
    """加载配置文件"""
    config_file = Path(config_path)

    if config_file.exists():
        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        # 返回默认配置
        return {
            "api_type": "ai",
            "api_configs": {
                "ai": {
                    "api_key": "",
                    "base_url": "https://api.deepseek.com/v1",
                    "model": "deepseek-chat",
                    "temperature": 0.3,
                    "max_tokens": 2000
                }
            },
            "translation_settings": {
                "use_cache": True,
                "batch_size": 10,
                "max_workers": 3,
                "mc_terminology": {
                    "Crafting": "合成",
                    "Item": "物品",
                    "Block": "方块",
                    "Enchantment": "附魔",
                    "Potion": "药水"
                }
            }
        }


def create_default_config():
    """创建默认配置文件"""
    if not Path("config_template.json").exists():
        print("错误: config_template.json 不存在")
        return

    import shutil
    shutil.copy("config_template.json", "user_config.json")
    print("已创建默认配置文件: user_config.json")
    print("请编辑此文件填入API密钥")


def main():
    """主入口函数"""
    parser = argparse.ArgumentParser(
        description="MC模组汉化工具 - 自动生成整合包汉化补丁资源包",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # GUI模式（默认）
  python main.py

  # 命令行模式
  python main.py --cli --mods "C:/HMCL/mods" --output "汉化补丁.zip"

  # 使用自定义配置
  python main.py --cli --mods "./mods" --config "my_config.json"

  # 手动指定 FTB Quests 任务书目录
  python main.py --cli --mods "./mods" --ftb-quests "./config/ftbquests/quests"

  # 内联式任务书生成后自动备份并替换 quests
  python main.py --cli --mods "./mods" --ftb-auto-import

  # 创建默认配置文件
  python main.py --init-config
        """
    )

    parser.add_argument('--cli', action='store_true',
                       help='使用命令行模式（默认为GUI模式）')

    parser.add_argument('--mods', type=str,
                       help='Mods文件夹路径')

    parser.add_argument('--output', type=str, default='汉化补丁.zip',
                       help='输出资源包路径（默认: 汉化补丁.zip）')

    parser.add_argument('--config', type=str, default='user_config.json',
                       help='配置文件路径（默认: user_config.json）')

    parser.add_argument('--api-type', type=str, choices=['ai', 'baidu', 'deepl'],
                       help='API类型')

    parser.add_argument('--api-key', type=str,
                       help='API密钥（覆盖配置文件）')

    parser.add_argument('--batch-size', type=int,
                       help='批量翻译条数（覆盖配置文件，默认使用配置值）')

    parser.add_argument('--max-workers', type=int,
                       help='并发请求数（覆盖配置文件，默认使用配置值）')

    parser.add_argument('--ftb-quests', type=str,
                       help='FTB Quests 的 quests 目录（默认从 mods 同级目录自动检测）')

    parser.add_argument('--ftb-output', type=str,
                       help='完整 quests 汉化目录的输出根目录（默认在主资源包旁生成）')

    parser.add_argument('--no-ftb-quests', action='store_true',
                       help='禁用 FTB Quests 任务书汉化')

    parser.add_argument('--ftb-auto-import', action='store_true',
                       help='内联/外部语言模式生成后，自动备份并替换 quests 目录')

    parser.add_argument('--pack-format', type=int, default=15,
                       help='资源包格式版本（默认: 15, 适用于MC 1.20.x）')

    parser.add_argument('--force', action='store_true',
                       help='强制翻译所有模组（包括已有中文的）')

    parser.add_argument('--verbose', '-v', action='store_true',
                       help='显示详细错误信息')

    parser.add_argument('--init-config', action='store_true',
                       help='创建默认配置文件')

    parser.add_argument('--version', action='version', version=f'MC模组汉化工具 v{APP_VERSION}')

    args = parser.parse_args()

    # 处理特殊命令
    if args.init_config:
        create_default_config()
        return 0

    # 选择模式
    if args.cli:
        if not args.mods:
            parser.error("命令行模式需要指定 --mods 参数")
        return main_cli(args)
    else:
        main_gui()
        return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n用户中断")
        sys.exit(1)
