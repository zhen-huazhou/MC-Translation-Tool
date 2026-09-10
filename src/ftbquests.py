"""FTB Quests quest-book detection, translation and patch generation.

Supported quest-book layouts:

* ``inline``: human-readable text is stored directly in chapter SNBT files.
  FTB Quests versions using this layout cannot be translated by a resource pack.
* ``localized_keys``: SNBT fields contain keys such as
  ``{quests.example.title}``. These keys are resolved by Minecraft's language
  manager, so translations can be merged into a normal resource pack.
* ``external_lang``: newer FTB Quests versions keep quest text in
  ``config/ftbquests/quests/lang/<locale>``. These files are server-side quest
  data and must be replaced in the quests directory.

A mixed quest book is also handled: localized key entries go to the resource
pack while inline or external language entries produce a complete replacement
``quests`` directory.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple


MODE_NONE = "none"
MODE_INLINE = "inline"
MODE_LOCALIZED_KEYS = "localized_keys"
MODE_EXTERNAL_LANG = "external_lang"
MODE_MIXED = "mixed"

DIRECT_TEXT_FIELDS = {
    "title",
    "subtitle",
    "description",
    "text",
    "quest_details",
    "completion_text",
    "hover_text",
}

_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_BRACED_KEY_PATTERN = re.compile(r"^\{\s*([A-Za-z0-9_.:-]+)\s*\}$")
_DOTTED_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_-]+){2,}$")
_RESOURCE_ID_PATTERN = re.compile(
    r"^[a-z0-9_.-]+:[a-z0-9_./-]+$", re.IGNORECASE
)


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class FTBTextEntry:
    """One inline translatable string found in a quest SNBT file."""

    key: str
    text: str
    relative_path: str
    field: str
    start: int
    end: int
    raw: str


@dataclass(frozen=True)
class FTBKeyReference:
    """A localized translation key found in an SNBT text field."""

    key: str
    translation_key: str
    relative_path: str
    field: str
    start: int
    end: int


@dataclass
class _LangValue:
    kind: str
    values: List[str]


@dataclass(frozen=True)
class _LangSlot:
    entry_key: str
    translation_key: str
    index: Optional[int]
    source_text: str


@dataclass
class FTBQuestsInfo:
    """Result of scanning an FTB Quests ``quests`` directory."""

    quests_dir: Path
    direct_files: Dict[str, str] = field(default_factory=dict)
    direct_entries: List[FTBTextEntry] = field(default_factory=list)
    key_references: List[FTBKeyReference] = field(default_factory=list)
    key_sources: Dict[str, str] = field(default_factory=dict)
    lang_source: Dict[str, _LangValue] = field(default_factory=dict)
    lang_existing: Dict[str, _LangValue] = field(default_factory=dict)
    lang_slots: List[_LangSlot] = field(default_factory=list)
    lang_layout: str = "none"
    warnings: List[str] = field(default_factory=list)

    @property
    def has_external_language(self) -> bool:
        return bool(self.lang_slots) and not self.key_references

    @property
    def requires_quests_folder(self) -> bool:
        return bool(self.direct_entries) or self.has_external_language

    @property
    def has_resourcepack_entries(self) -> bool:
        return bool(self._localized_entry_keys())

    @property
    def mode(self) -> str:
        inline = bool(self.direct_entries)
        localized = bool(self.key_references)
        external = self.has_external_language
        category_count = int(inline) + int(localized) + int(external)
        if category_count > 1:
            return MODE_MIXED
        if localized:
            return MODE_LOCALIZED_KEYS
        if external:
            return MODE_EXTERNAL_LANG
        if inline:
            return MODE_INLINE
        return MODE_NONE

    def translation_entries(self) -> Dict[str, str]:
        entries = {entry.key: entry.text for entry in self.direct_entries}
        for entry_key, translation_key in self._localized_entry_keys().items():
            entries[entry_key] = self.key_sources[translation_key]
        entries.update({slot.entry_key: slot.source_text for slot in self.lang_slots})
        return entries

    @property
    def translatable_count(self) -> int:
        return len(self.translation_entries())

    def build_resourcepack_translations(
        self, translations: Mapping[str, str]
    ) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for entry_key, translation_key in self._localized_entry_keys().items():
            translated = translations.get(entry_key)
            if isinstance(translated, str) and translated.strip():
                result[translation_key] = translated
        return result

    def _localized_entry_keys(self) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for reference in self.key_references:
            if reference.translation_key not in self.key_sources:
                continue
            entry_key = f"ftbq:key:{reference.translation_key}"
            result.setdefault(entry_key, reference.translation_key)
        return result


@dataclass(frozen=True)
class FTBQuestsFolderResult:
    path: str
    translated_count: int
    direct_count: int
    lang_count: int


@dataclass(frozen=True)
class FTBQuestsImportResult:
    target_path: str
    backup_path: str


def get_ftb_mode_label(mode: str) -> str:
    return {
        MODE_INLINE: "内联式 SNBT",
        MODE_LOCALIZED_KEYS: "本地化键（可资源包覆盖）",
        MODE_EXTERNAL_LANG: "外部语言文件（需替换 quests）",
        MODE_MIXED: "混合模式",
        MODE_NONE: "未检测到可翻译文本",
    }.get(mode, mode)

def detect_ftbquests_directory(mods_path: str) -> Optional[Path]:
    """Infer the quests directory from a typical instance ``mods`` folder."""

    try:
        mods_dir = Path(mods_path).expanduser().resolve()
    except OSError:
        return None

    candidates = (
        mods_dir.parent / "config" / "ftbquests" / "quests",
        mods_dir / "config" / "ftbquests" / "quests",
        mods_dir.parent / "ftbquests" / "quests",
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def resolve_ftbquests_directory(path: str) -> Path:
    """Resolve a user-selected directory to the actual ``quests`` folder."""

    selected = Path(path).expanduser()
    if not selected.exists():
        raise FileNotFoundError(f"FTB Quests 目录不存在: {selected}")
    if not selected.is_dir():
        raise NotADirectoryError(f"FTB Quests 路径不是目录: {selected}")

    candidates = (
        selected,
        selected / "quests",
        selected / "ftbquests" / "quests",
        selected / "config" / "ftbquests" / "quests",
    )
    for candidate in candidates:
        if (candidate / "chapters").is_dir() or (candidate / "lang").is_dir():
            return candidate.resolve()

    if selected.name.lower() == "quests":
        return selected.resolve()
    raise ValueError(
        "无法识别 FTB Quests 目录，请选择 config/ftbquests/quests 文件夹"
    )


class FTBQuestsScanner:
    """Detect and scan inline, localized-key and external-language quests."""

    def __init__(self, quests_dir: str):
        self.quests_dir = resolve_ftbquests_directory(quests_dir)

    def scan(self) -> FTBQuestsInfo:
        info = FTBQuestsInfo(quests_dir=self.quests_dir)

        for file_path in sorted(self.quests_dir.rglob("*.snbt")):
            relative_path = file_path.relative_to(self.quests_dir)
            if relative_path.parts and relative_path.parts[0].lower() == "lang":
                continue
            try:
                text = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as error:
                info.warnings.append(f"无法读取 {relative_path}: {error}")
                continue

            entries, references = self._classify_direct_fields(
                text, relative_path.as_posix()
            )
            if entries or references:
                info.direct_files[relative_path.as_posix()] = text
                info.direct_entries.extend(entries)
                info.key_references.extend(references)

        try:
            info.lang_source, info.lang_layout = _load_config_language(
                self.quests_dir, "en_us"
            )
        except (OSError, UnicodeDecodeError, ValueError) as error:
            info.warnings.append(f"无法读取 FTB Quests 英文语言文件: {error}")

        try:
            info.lang_existing, _ = _load_config_language(
                self.quests_dir, "zh_cn"
            )
        except (OSError, UnicodeDecodeError, ValueError) as error:
            info.warnings.append(
                f"无法读取 FTB Quests 已有中文语言文件: {error}"
            )

        config_sources = _flatten_language_values(info.lang_source)
        resourcepack_sources = _discover_resourcepack_language_sources(
            self.quests_dir
        )
        info.key_sources.update(resourcepack_sources)
        # A pack-local language file is the most specific source and wins.
        info.key_sources.update(config_sources)

        missing_keys = {
            reference.translation_key
            for reference in info.key_references
            if reference.translation_key not in info.key_sources
        }
        if missing_keys:
            preview = ", ".join(sorted(missing_keys)[:5])
            if len(missing_keys) > 5:
                preview += ", ..."
            info.warnings.append(
                f"有 {len(missing_keys)} 个本地化键未找到英文原文: {preview}"
            )

        info.lang_slots = _build_lang_slots(
            info.lang_source, info.lang_existing
        )
        return info

    @staticmethod
    def _classify_direct_fields(
        text: str, relative_path: str
    ) -> Tuple[List[FTBTextEntry], List[FTBKeyReference]]:
        tokens = _tokenize(text)
        entries: List[FTBTextEntry] = []
        references: List[FTBKeyReference] = []

        for index, token in enumerate(tokens):
            if token.kind not in ("STRING", "WORD"):
                continue
            if index + 1 >= len(tokens) or tokens[index + 1].kind != ":":
                continue

            field_name = token.value.lower()
            if field_name not in DIRECT_TEXT_FIELDS:
                continue

            value_index = index + 2
            if value_index >= len(tokens):
                continue
            value_token = tokens[value_index]
            if value_token.kind == "STRING":
                _classify_value(
                    entries,
                    references,
                    text,
                    relative_path,
                    field_name,
                    value_token,
                )
                continue

            if value_token.kind != "[":
                continue

            depth = 0
            for list_token in tokens[value_index:]:
                if list_token.kind == "[":
                    depth += 1
                    continue
                if list_token.kind == "]":
                    depth -= 1
                    if depth == 0:
                        break
                    continue
                if depth == 1 and list_token.kind == "STRING":
                    _classify_value(
                        entries,
                        references,
                        text,
                        relative_path,
                        field_name,
                        list_token,
                    )

        return entries, references

class FTBQuestsFolderGenerator:
    """Generate a complete replacement ``quests`` directory."""

    def generate(
        self,
        info: FTBQuestsInfo,
        translations: Mapping[str, str],
        output_path: str,
        cancel_check=None,
    ) -> Optional[FTBQuestsFolderResult]:
        if not info.requires_quests_folder:
            return None

        direct_replacements = _build_direct_replacements(info, translations)
        lang_values = _merge_lang_values(info, translations)
        lang_count = _count_changed_lang_values(info, translations)

        if not direct_replacements and not lang_count:
            return None

        if cancel_check and cancel_check():
            from translator import TranslationCancelled

            raise TranslationCancelled("FTB Quests 补丁生成已取消")

        output_root = _resolve_quests_output_root(output_path)
        source_dir = info.quests_dir.resolve()
        destination = (output_root / "quests").resolve()
        if destination == source_dir or source_dir in destination.parents:
            raise ValueError("FTB Quests 输出目录不能位于原 quests 目录内部")

        output_root.mkdir(parents=True, exist_ok=True)
        temporary_dir = output_root / f".quests.{os.getpid()}.tmp"
        if temporary_dir.exists():
            shutil.rmtree(temporary_dir)

        try:
            shutil.copytree(source_dir, temporary_dir)

            for relative_path, content in direct_replacements.items():
                target_file = temporary_dir / relative_path
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_text(content, encoding="utf-8")

            if info.has_external_language and lang_count:
                _write_config_language(
                    temporary_dir, info.lang_layout, lang_values
                )

            if destination.exists():
                shutil.rmtree(destination)
            os.replace(str(temporary_dir), str(destination))
        except Exception:
            if temporary_dir.exists():
                shutil.rmtree(temporary_dir, ignore_errors=True)
            raise

        return FTBQuestsFolderResult(
            path=str(destination),
            translated_count=len(direct_replacements) + lang_count,
            direct_count=len(direct_replacements),
            lang_count=lang_count,
        )


class FTBQuestsImporter:
    """Safely replace an instance quests directory with generated output."""

    @staticmethod
    def import_quests(
        generated_quests: str,
        target_quests: str,
        cancel_check=None,
    ) -> FTBQuestsImportResult:
        source = Path(generated_quests).expanduser().resolve()
        target = Path(target_quests).expanduser().resolve()

        if not source.is_dir():
            raise FileNotFoundError(f"生成的任务书目录不存在: {source}")
        if not target.is_dir():
            raise FileNotFoundError(f"目标任务书目录不存在: {target}")
        if (
            source == target
            or source in target.parents
            or target in source.parents
        ):
            raise ValueError("生成目录与目标 quests 目录不能互相嵌套")

        if cancel_check and cancel_check():
            from translator import TranslationCancelled

            raise TranslationCancelled("FTB Quests 一键导入已取消")

        backup = target.parent / "quests_backup"
        if backup.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = target.parent / f"quests_backup_{timestamp}"

        os.replace(str(target), str(backup))
        try:
            shutil.copytree(source, target)
        except Exception:
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            if backup.exists() and not target.exists():
                os.replace(str(backup), str(target))
            raise

        return FTBQuestsImportResult(
            target_path=str(target),
            backup_path=str(backup),
        )


def build_ftb_output_path(primary_output: str) -> str:
    """Build a companion quests output directory beside the resource pack."""

    path = Path(primary_output)
    if path.exists() and path.is_dir():
        return str(path / "FTBQuests_汉化补丁")
    if not path.suffix:
        return str(path / "FTBQuests_汉化补丁")
    return str(path.with_name(f"{path.stem}_FTBQuests"))


def build_resourcepack_files(
    info: FTBQuestsInfo,
    translations: Mapping[str, str],
) -> Dict[str, str]:
    """Build language files to merge into the main resource pack."""

    values = info.build_resourcepack_translations(translations)
    if not values:
        return {}
    return {
        "assets/ftbquests/lang/zh_cn.json": json.dumps(
            values, ensure_ascii=False, indent=2
        )
        + "\n"
    }


def _classify_value(
    entries: List[FTBTextEntry],
    references: List[FTBKeyReference],
    source_text: str,
    relative_path: str,
    field_name: str,
    token: _Token,
):
    translation_key = _extract_translation_key(token.value)
    if translation_key:
        references.append(
            FTBKeyReference(
                key=f"ftbq:keyref:{relative_path}:{token.start}",
                translation_key=translation_key,
                relative_path=relative_path,
                field=field_name,
                start=token.start,
                end=token.end,
            )
        )
        return

    if not _is_translatable_text(token.value):
        return
    entry_key = f"ftbq:direct:{relative_path}:{token.start}:{len(entries)}"
    entries.append(
        FTBTextEntry(
            key=entry_key,
            text=token.value,
            relative_path=relative_path,
            field=field_name,
            start=token.start,
            end=token.end,
            raw=source_text[token.start:token.end],
        )
    )


def _extract_translation_key(text: str) -> Optional[str]:
    stripped = text.strip()
    braced = _BRACED_KEY_PATTERN.match(stripped)
    if braced:
        return braced.group(1)
    if _DOTTED_KEY_PATTERN.match(stripped):
        return stripped
    return None


def _build_direct_replacements(
    info: FTBQuestsInfo,
    translations: Mapping[str, str],
) -> Dict[str, str]:
    replacements_by_file: Dict[str, List[Tuple[int, int, str]]] = {}
    for entry in info.direct_entries:
        translated = translations.get(entry.key)
        if not isinstance(translated, str) or not translated.strip():
            continue
        if translated == entry.text:
            continue
        replacements_by_file.setdefault(entry.relative_path, []).append(
            (entry.start, entry.end, json.dumps(translated, ensure_ascii=False))
        )

    entry_lookup = {
        (entry.relative_path, entry.start, entry.end): entry
        for entry in info.direct_entries
    }
    result: Dict[str, str] = {}
    for relative_path, replacements in replacements_by_file.items():
        content = info.direct_files[relative_path]
        valid_replacements = []
        for start, end, replacement in replacements:
            matching_entry = entry_lookup.get((relative_path, start, end))
            if matching_entry and content[start:end] == matching_entry.raw:
                valid_replacements.append((start, end, replacement))

        if not valid_replacements:
            continue
        for start, end, replacement in sorted(valid_replacements, reverse=True):
            content = content[:start] + replacement + content[end:]
        result[relative_path] = content
    return result

def _load_config_language(
    quests_dir: Path, locale: str
) -> Tuple[Dict[str, _LangValue], str]:
    flat_file = quests_dir / "lang" / f"{locale}.snbt"
    locale_dir = quests_dir / "lang" / locale
    combined: Dict[str, _LangValue] = {}
    layouts = []

    if flat_file.is_file():
        layouts.append("flat")
        combined.update(_parse_lang_file(flat_file.read_text(encoding="utf-8")))

    if locale_dir.is_dir():
        layouts.append("directory")
        for file_path in sorted(locale_dir.rglob("*.snbt")):
            combined.update(
                _parse_lang_file(file_path.read_text(encoding="utf-8"))
            )

    if "directory" in layouts:
        layout = "directory"
    elif "flat" in layouts:
        layout = "flat"
    else:
        layout = "none"
    return combined, layout


def _flatten_language_values(
    values: Mapping[str, _LangValue]
) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for key, value in values.items():
        if value.kind == "string":
            result[key] = value.values[0]
        elif value.values:
            result[key] = "\n".join(value.values)
    return result


def _discover_resourcepack_language_sources(
    quests_dir: Path,
) -> Dict[str, str]:
    try:
        instance_root = quests_dir.parents[2]
    except IndexError:
        return {}

    result: Dict[str, str] = {}
    resourcepack_dir = instance_root / "resourcepacks"
    if not resourcepack_dir.is_dir():
        return result

    for file_path in sorted(resourcepack_dir.rglob("en_us.json")):
        relative = file_path.relative_to(resourcepack_dir)
        if "assets" not in relative.parts or "lang" not in relative.parts:
            continue
        _merge_json_language_file(file_path, result)

    for archive_path in sorted(resourcepack_dir.glob("*.zip")):
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                for member_name in archive.namelist():
                    lower_name = member_name.lower()
                    if not lower_name.endswith("/lang/en_us.json"):
                        continue
                    parts = Path(member_name).parts
                    if "assets" not in parts:
                        continue
                    with archive.open(member_name) as file:
                        _merge_json_language_data(file.read(), result)
        except (OSError, zipfile.BadZipFile, json.JSONDecodeError):
            continue

    return result


def _merge_json_language_file(path: Path, result: Dict[str, str]):
    try:
        _merge_json_language_data(path.read_bytes(), result)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return


def _merge_json_language_data(data: bytes, result: Dict[str, str]):
    values = json.loads(data.decode("utf-8"))
    if not isinstance(values, dict):
        return
    for key, value in values.items():
        if isinstance(value, str):
            result[str(key)] = value


def _build_lang_slots(
    source: Mapping[str, _LangValue],
    existing: Mapping[str, _LangValue],
) -> List[_LangSlot]:
    slots: List[_LangSlot] = []
    for translation_key, source_value in source.items():
        existing_value = existing.get(translation_key)
        if source_value.kind == "string":
            if existing_value and _has_cjk(existing_value.values[0]):
                continue
            source_text = source_value.values[0]
            if not _is_translatable_text(source_text):
                continue
            slots.append(
                _LangSlot(
                    entry_key=f"ftbq:lang:{translation_key}",
                    translation_key=translation_key,
                    index=None,
                    source_text=source_text,
                )
            )
            continue

        for index, source_text in enumerate(source_value.values):
            if not _is_translatable_text(source_text):
                continue
            if (
                existing_value
                and existing_value.kind == "list"
                and index < len(existing_value.values)
                and _has_cjk(existing_value.values[index])
            ):
                continue
            slots.append(
                _LangSlot(
                    entry_key=f"ftbq:lang:{translation_key}:{index}",
                    translation_key=translation_key,
                    index=index,
                    source_text=source_text,
                )
            )
    return slots


def _merge_lang_values(
    info: FTBQuestsInfo, translations: Mapping[str, str]
) -> Dict[str, _LangValue]:
    result: Dict[str, _LangValue] = {}
    slot_lookup = {slot.entry_key: slot for slot in info.lang_slots}

    for translation_key, source_value in info.lang_source.items():
        existing_value = info.lang_existing.get(translation_key)
        if source_value.kind == "string":
            source_text = source_value.values[0]
            existing_text = ""
            if existing_value and existing_value.kind == "string":
                existing_text = existing_value.values[0]
            if _has_cjk(existing_text):
                value = existing_text
            else:
                slot = slot_lookup.get(f"ftbq:lang:{translation_key}")
                value = (
                    translations.get(slot.entry_key, source_text)
                    if slot
                    else source_text
                )
            result[translation_key] = _LangValue("string", [value])
            continue

        values: List[str] = []
        for index, source_text in enumerate(source_value.values):
            existing_text = ""
            if (
                existing_value
                and existing_value.kind == "list"
                and index < len(existing_value.values)
            ):
                existing_text = existing_value.values[index]
            if _has_cjk(existing_text):
                value = existing_text
            else:
                slot = slot_lookup.get(
                    f"ftbq:lang:{translation_key}:{index}"
                )
                value = (
                    translations.get(slot.entry_key, source_text)
                    if slot
                    else source_text
                )
            values.append(value)
        result[translation_key] = _LangValue("list", values)

    for translation_key, existing_value in info.lang_existing.items():
        if translation_key not in result:
            result[translation_key] = existing_value
    return result


def _count_changed_lang_values(
    info: FTBQuestsInfo, translations: Mapping[str, str]
) -> int:
    return sum(
        1
        for slot in info.lang_slots
        if isinstance(translations.get(slot.entry_key), str)
        and translations.get(slot.entry_key) != slot.source_text
    )


def _write_config_language(
    quests_dir: Path,
    layout: str,
    values: Mapping[str, _LangValue],
):
    lang_dir = quests_dir / "lang"
    flat_path = lang_dir / "zh_cn.snbt"
    directory_path = lang_dir / "zh_cn"
    for existing in (flat_path, directory_path):
        if existing.is_dir():
            shutil.rmtree(existing)
        elif existing.exists():
            existing.unlink()

    if layout == "directory":
        target = directory_path / "translations.snbt"
    else:
        target = flat_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_serialize_lang_file(values), encoding="utf-8")


def _resolve_quests_output_root(output_path: str) -> Path:
    path = Path(output_path).expanduser()
    if path.suffix.lower() == ".zip":
        path = path.with_suffix("")
    return path.resolve()

def _parse_lang_file(text: str) -> Dict[str, _LangValue]:
    tokens = _tokenize(text)
    if not tokens:
        return {}

    start = 1 if tokens[0].kind == "{" else 0
    end = len(tokens) - 1 if tokens[-1].kind == "}" else len(tokens)
    result: Dict[str, _LangValue] = {}
    index = start

    while index < end:
        key_token = tokens[index]
        if key_token.kind not in ("STRING", "WORD"):
            index += 1
            continue
        if index + 1 >= end or tokens[index + 1].kind != ":":
            index += 1
            continue

        translation_key = key_token.value
        value_index = index + 2
        if value_index >= end:
            break
        value_token = tokens[value_index]

        if value_token.kind == "STRING":
            result[translation_key] = _LangValue("string", [value_token.value])
            index = value_index + 1
            continue

        if value_token.kind == "[":
            values: List[str] = []
            depth = 0
            cursor = value_index
            while cursor < end:
                current = tokens[cursor]
                if current.kind == "[":
                    depth += 1
                elif current.kind == "]":
                    depth -= 1
                    if depth == 0:
                        cursor += 1
                        break
                elif depth == 1 and current.kind == "STRING":
                    values.append(current.value)
                cursor += 1
            result[translation_key] = _LangValue("list", values)
            index = cursor
            continue

        index = _skip_value(tokens, value_index, end)
    return result


def _skip_value(tokens: List[_Token], index: int, end: int) -> int:
    depth = 0
    while index < end:
        token = tokens[index]
        if token.kind in ("{", "["):
            depth += 1
        elif token.kind in ("}", "]"):
            if depth == 0:
                break
            depth -= 1
        elif token.kind == "," and depth == 0:
            return index + 1
        index += 1
    return index


def _serialize_lang_file(values: Mapping[str, _LangValue]) -> str:
    lines = ["{"]
    items = list(values.items())
    for item_index, (translation_key, value) in enumerate(items):
        comma = "," if item_index < len(items) - 1 else ""
        encoded_key = json.dumps(translation_key, ensure_ascii=False)
        if value.kind == "string":
            encoded_value = json.dumps(value.values[0], ensure_ascii=False)
            lines.append(f"  {encoded_key}: {encoded_value}{comma}")
            continue

        lines.append(f"  {encoded_key}: [")
        for value_index, text in enumerate(value.values):
            value_comma = "," if value_index < len(value.values) - 1 else ""
            lines.append(
                f"    {json.dumps(text, ensure_ascii=False)}{value_comma}"
            )
        lines.append(f"  ]{comma}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _has_cjk(text: str) -> bool:
    return bool(text and _CJK_PATTERN.search(text))


def _is_translatable_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped or _has_cjk(stripped):
        return False
    if _extract_translation_key(stripped):
        return False
    if _RESOURCE_ID_PATTERN.match(stripped):
        return False
    if "://" in stripped or stripped.startswith("/"):
        return False
    if not re.search(r"[A-Za-z]", stripped):
        return False
    return True


def _tokenize(text: str) -> List[_Token]:
    tokens: List[_Token] = []
    index = 0
    punctuation = set("{}[]:,")

    while index < len(text):
        character = text[index]
        if character.isspace() or character == "\ufeff":
            index += 1
            continue

        if character in punctuation:
            tokens.append(_Token(character, character, index, index + 1))
            index += 1
            continue

        if character in ('"', "'"):
            start = index
            quote = character
            index += 1
            escaped = False
            while index < len(text):
                current = text[index]
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == quote:
                    index += 1
                    break
                index += 1
            raw = text[start:index]
            tokens.append(_Token("STRING", _decode_quoted(raw), start, index))
            continue

        start = index
        while (
            index < len(text)
            and not text[index].isspace()
            and text[index] not in punctuation
            and text[index] not in ('"', "'")
        ):
            index += 1
        if index == start:
            index += 1
            continue
        tokens.append(_Token("WORD", text[start:index], start, index))

    return tokens


def _decode_quoted(raw: str) -> str:
    if len(raw) < 2:
        return raw
    if raw[0] == '"':
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

    body = raw[1:-1]
    escapes = {
        '"': '"',
        "'": "'",
        "\\": "\\",
        "/": "/",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }
    result: List[str] = []
    index = 0
    while index < len(body):
        character = body[index]
        if character != "\\" or index + 1 >= len(body):
            result.append(character)
            index += 1
            continue

        escaped = body[index + 1]
        if escaped == "u" and index + 5 < len(body):
            try:
                result.append(chr(int(body[index + 2:index + 6], 16)))
                index += 6
                continue
            except ValueError:
                pass
        result.append(escapes.get(escaped, escaped))
        index += 2
    return "".join(result)


def test_ftbquests():
    """Self-test inline, localized-key and external-language detection."""

    import tempfile

    with tempfile.TemporaryDirectory(prefix="ftbquests_test_") as temp:
        instance = Path(temp)
        root = instance / "config" / "ftbquests" / "quests"
        (root / "chapters").mkdir(parents=True)
        (root / "lang").mkdir(parents=True)
        (root / "chapters" / "inline.snbt").write_text(
            '{\n  title: "Getting Started"\n'
            '  description: ["Welcome to the pack"]\n}\n',
            encoding="utf-8",
        )

        scanner = FTBQuestsScanner(str(root))
        info = scanner.scan()
        assert info.mode == MODE_INLINE

        translations = {
            entry_key: f"内联译文{index}"
            for index, entry_key in enumerate(
                info.translation_entries(), 1
            )
        }
        result = FTBQuestsFolderGenerator().generate(
            info, translations, str(instance / "inline_output")
        )
        assert result is not None
        assert (Path(result.path) / "chapters" / "inline.snbt").exists()

        key_root = instance / "key_pack" / "config" / "ftbquests" / "quests"
        (key_root / "chapters").mkdir(parents=True)
        (key_root / "lang").mkdir(parents=True)
        (key_root / "chapters" / "keyed.snbt").write_text(
            '{\n  title: "{quests.demo.title}"\n'
            '  description: ["{quests.demo.desc}"]\n}\n',
            encoding="utf-8",
        )
        (key_root / "lang" / "en_us.snbt").write_text(
            '{\n  "quests.demo.title": "Keyed Quest"\n'
            '  "quests.demo.desc": "Use resource pack translation"\n}\n',
            encoding="utf-8",
        )
        key_info = FTBQuestsScanner(str(key_root)).scan()
        assert key_info.mode == MODE_LOCALIZED_KEYS
        key_translations = {
            entry_key: f"键译文{index}"
            for index, entry_key in enumerate(
                key_info.translation_entries(), 1
            )
        }
        files = build_resourcepack_files(key_info, key_translations)
        assert "assets/ftbquests/lang/zh_cn.json" in files
        assert not key_info.requires_quests_folder

        external_root = (
            instance / "external" / "config" / "ftbquests" / "quests"
        )
        (external_root / "chapters").mkdir(parents=True)
        (external_root / "lang").mkdir(parents=True)
        (external_root / "lang" / "en_us.snbt").write_text(
            '{\n  "quests.external.title": "External Text"\n}\n',
            encoding="utf-8",
        )
        external_info = FTBQuestsScanner(str(external_root)).scan()
        assert external_info.mode == MODE_EXTERNAL_LANG
        assert external_info.requires_quests_folder


if __name__ == "__main__":
    test_ftbquests()
