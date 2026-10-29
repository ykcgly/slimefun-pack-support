#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Slimefun 材质包 —— 新增材质导入工具

用法（在任意目录执行均可，脚本会自己定位材质包根目录）:

    python tools/add_texture.py            # 交互式向导
    python tools/add_texture.py --help     # 查看非交互参数
    python tools/add_texture.py --verify   # 校验清单与产物是否一致（不改文件）

双击运行:
    如果 .py 关联的是 pythonw.exe（无控制台），脚本会自动用同目录的 python.exe
    另开一个控制台窗口重跑自己，并在结束时停住等待回车，避免窗口一闪而过。
    自动化场景可加 --no-pause 或设置环境变量 SF_NO_PAUSE=1。

流程:
    1. 自动创建 tools/import/ ，把 PNG 按「Slimefun 物品 ID」命名后放进去
       （例: PORTABLE_CRAFTER.png / DYED_SMALL_BACKPACK_LIME.png）
    2. 脚本读取 assets/ 下已有的附属命名空间供选择
    3. 选择目标类型（头颅 / 方块 / 物品），物品与方块需再选一个原版基底
    4. 选完立即生成，不再二次确认：
         - 贴图     -> assets/<附属>/textures/<子目录>/<id>.png
         - 模型     -> assets/<附属>/models/<子目录>/<id>.json   （parent: item/generated）
         - 注册清单 -> data/itemdefs/<基底>.json 追加 { "id", "model" }
         - 重建产物 -> assets/minecraft/items/<基底>.json（含 1.21.6+ overlay 的 player_head）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

# ----------------------------------------------------------------- 常量与路径 --
SCRIPT_DIR = Path(__file__).resolve().parent
IMPORT_DIR = SCRIPT_DIR / "import"

MAX_NESTING = 250  # Minecraft(Gson) 对物品定义文件的嵌套上限 255，留余量

ID_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
IMAGE_SUFFIXES = {".png"}
OTHER_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tga"}

# 方块类基底的名字启发式（仅用于菜单归类，选 'a' 可看全部基底）
BLOCK_HINT = re.compile(
    r"(_block$|_ore$|_planks$|_log$|_wood$|_hyphae$|_stairs$|_slab$|_wall$|"
    r"_fence(_gate)?$|_wool$|_terracotta$|_concrete$|_glass(_pane)?$|_bricks?$|"
    r"_table$|furnace|smoker|chest|hopper|dispenser|dropper|observer|piston|"
    r"cauldron|anvil|barrel|beacon|composter|lectern|loom|stonecutter|grindstone|"
    r"crafter|decorated_pot|bookshelf|sandstone|quartz|prismarine|purpur|mud|"
    r"note_block|jukebox|target|lodestone|anchor|beehive|bee_nest|glowstone|"
    r"lantern|lamp|coral|shulker_box|bed$|banner|cartography|smithing|fletching|"
    r"crafting_table|enchanting_table|bell|chain|flower_pot|item_frame|"
    r"sign$|_sign$|dried_ghast|sculk|deepslate|blackstone|basalt|crying|"
    r"campfire|fire$|froglight|amethyst|calcite|tuff|dripstone|pointed_dripstone)"
)

# 模型 JSON 里 parent 为 item/generated 的才被当作“单层图标”
GENERATED_PARENT = "item/generated"

LOG_FILE = SCRIPT_DIR / "add_texture.log"


# ------------------------------------------------------- 双击运行兼容（Windows） --
def _is_windows() -> bool:
    return os.name == "nt"


def _kernel32():
    if not _is_windows():
        return None
    try:
        import ctypes  # 仅 Windows 需要，延迟导入

        return ctypes.windll.kernel32
    except Exception:
        return None


def _has_console() -> bool:
    """当前进程是否挂着控制台窗口（pythonw 启动时为 False）。"""
    k32 = _kernel32()
    if k32 is None:
        return True
    try:
        return bool(k32.GetConsoleWindow())
    except Exception:
        return True


def _console_is_ours() -> bool:
    """控制台里只有本进程 ⇒ 是双击/explorer 新建的窗口，结束后会立即关闭。"""
    k32 = _kernel32()
    if k32 is None:
        return False
    try:
        import ctypes

        buf = (ctypes.c_uint * 8)()
        count = int(k32.GetConsoleProcessList(buf, 8))
        return count <= 1
    except Exception:
        return False


def _console_python() -> str | None:
    """找一个带控制台的 python.exe（区别于 pythonw.exe）。"""
    exe = Path(sys.executable)
    sibling = exe.with_name("python.exe")
    if sibling.exists() and sibling != exe:
        return str(sibling)
    if exe.name.lower() == "python.exe":
        return str(exe)
    for name in ("python", "python3", "py"):
        found = shutil.which(name)
        if found and not found.lower().endswith("pythonw.exe"):
            return found
    return None


def _relaunch_in_console(argv: list[str]) -> int | None:
    """无控制台（pythonw.exe）启动时，另开一个控制台窗口重跑自己。

    返回 None 表示继续在当前进程运行；返回 int 表示已交给子进程，应当直接退出。
    """
    if not _is_windows():
        return None
    # 只有在“标准流不可用”时才重启：正常终端、CI 管道调用都不打扰
    if sys.stdout is not None and sys.stdin is not None:
        return None

    console_py = _console_python()
    if console_py is None:
        _setup_log_fallback()
        print("[!] 当前没有控制台，且找不到 python.exe；本次输出只写入日志文件:")
        print(f"    {LOG_FILE}")
        return None

    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    try:
        subprocess.Popen(
            [console_py, str(Path(__file__).resolve()), *argv],
            cwd=str(SCRIPT_DIR),
            creationflags=creationflags,
            close_fds=True,
        )
    except OSError:
        _setup_log_fallback()
        return None
    return 0


class _Tee:
    """同时写文件与原始流，保证无窗口时也不丢输出。"""

    def __init__(self, stream, path: Path) -> None:
        self._stream = stream
        try:
            path.write_text("", encoding="utf-8")
            self._file = open(path, "a", encoding="utf-8", newline="")
        except OSError:
            self._file = None

    def write(self, text: str) -> int:
        if self._file:
            try:
                self._file.write(text)
                self._file.flush()
            except OSError:
                pass
        if self._stream:
            try:
                self._stream.write(text)
            except (UnicodeEncodeError, OSError):
                pass
        return len(text)

    def flush(self) -> None:
        for stream in (self._file, self._stream):
            try:
                stream.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        try:
            return bool(self._stream and self._stream.isatty())
        except Exception:
            return False


def _setup_log_fallback() -> None:
    """把 stdout/stderr 同时镜像到 add_texture.log（无控制台场景）。"""
    if isinstance(sys.stdout, _Tee):
        return
    sys.stdout = _Tee(sys.stdout, LOG_FILE)
    sys.stderr = _Tee(sys.stderr, LOG_FILE)


def _harden_streams() -> None:
    """控制台编码不是 UTF-8 时（如 cp936/cp437）避免中文字符把程序打崩。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def _should_pause(argv: list[str]) -> bool:
    """双击场景结束前停住，避免窗口一闪而过；终端/管道运行时不暂停。"""
    if "--no-pause" in argv or os.environ.get("SF_NO_PAUSE"):
        return False
    if sys.stdin is None or not getattr(sys.stdin, "isatty", lambda: False)():
        return False
    if not _is_windows():
        return False
    return _console_is_ours()


def _pause() -> None:
    try:
        input("\n按回车键关闭窗口 ...")
    except (EOFError, KeyboardInterrupt, OSError):
        pass


def _message_box(text: str) -> None:
    """连日志都写不了时的最后提示手段（无控制台且无法重启）。"""
    if not _is_windows():
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, "add_texture.py", 0x10)
    except Exception:
        pass


# -------------------------------------------------------------------- 基础 IO --
class Pack:
    """材质包根目录的定位与读写。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.assets = root / "assets"
        self.itemdefs = root / "data" / "itemdefs"
        self.items_out = self.assets / "minecraft" / "items"
        self.overlay_items = root / "overlay_1_21_6_plus" / "assets" / "minecraft" / "items"

    @staticmethod
    def locate(start: Path) -> "Pack":
        for cand in (start, start.parent, start.parent.parent):
            if (cand / "assets").is_dir() and (cand / "data" / "itemdefs").is_dir():
                return Pack(cand)
        raise SystemExit(
            "[x] 找不到材质包根目录（需要同时存在 assets/ 与 data/itemdefs/）。\n"
            f"    已尝试: {start} 及其上两级目录\n"
            "    请把本脚本放在材质包根目录，或根目录下的 tools/ 子目录里再运行。"
        )

    # -- 附属（命名空间） ---------------------------------------------------
    def addons(self) -> list[str]:
        names = sorted(p.name for p in self.assets.iterdir() if p.is_dir())
        # minecraft 是原版覆盖层，放到最后
        return [n for n in names if n != "minecraft"] + (["minecraft"] if "minecraft" in names else [])

    def addon_stats(self, ns: str) -> tuple[int, int]:
        models = sum(1 for _ in (self.assets / ns / "models").rglob("*.json")) if (self.assets / ns / "models").is_dir() else 0
        tex = sum(1 for _ in (self.assets / ns / "textures").rglob("*.png")) if (self.assets / ns / "textures").is_dir() else 0
        return models, tex

    # -- 清单 ---------------------------------------------------------------
    def manifest_path(self, base: str) -> Path:
        return self.itemdefs / f"{base}.json"

    def load_manifest(self, base: str) -> dict:
        return json.loads(self.manifest_path(base).read_text(encoding="utf-8"))

    def save_manifest(self, base: str, data: dict) -> None:
        write_json(self.manifest_path(base), data)

    def manifest_bases(self) -> list[str]:
        return sorted(p.stem for p in self.itemdefs.glob("*.json"))

    def id_owner_map(self) -> dict[str, list[str]]:
        """{物品ID: [出现在哪些基底清单里]}，用于发现跨基底重复注册。"""
        owner: dict[str, list[str]] = {}
        for path in self.itemdefs.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for entry in data.get("entries", []) or []:
                item_id = entry.get("id")
                if isinstance(item_id, str):
                    owner.setdefault(item_id, []).append(path.stem)
        return owner

    # -- 资源路径换算 -------------------------------------------------------
    def model_file(self, ref: str) -> Path:
        ns, _, rel = ref.partition(":")
        return self.assets / (ns or "minecraft") / "models" / f"{rel}.json"

    def texture_file(self, ref: str) -> Path:
        ns, _, rel = ref.partition(":")
        return self.assets / (ns or "minecraft") / "textures" / f"{rel}.png"

def write_json(path: Path, data: dict) -> None:
    """按本仓库的既有风格写 JSON：2 空格缩进、CRLF、无 BOM、结尾无多余换行。"""
    text = json.dumps(data, indent=2, ensure_ascii=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text.replace("\n", "\r\n"))


def write_json_compact(path: Path, data: dict) -> None:
    """写单行模型 JSON（本仓库 addon 模型均为单行紧凑格式）。"""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


# ------------------------------------------------------- 物品定义重建（核心） --
def depth(node) -> int:
    """最深 JSON 嵌套层数（叶子=1），与旧构建器 GetDepth 一致。"""
    if isinstance(node, dict):
        return 1 + max((depth(v) for v in node.values()), default=0)
    if isinstance(node, list):
        return 1 + max((depth(v) for v in node), default=0)
    return 1


def snbt_variants(item_id: str) -> list[str]:
    """custom_data 组件的两种 SNBT 写法（必须都能被游戏解析，见 README）。"""
    inner = '{"slimefun:slimefun_item":"%s"}' % item_id
    return [
        '{PublicBukkitValues:%s}' % inner,          # 嵌套复合
        "{PublicBukkitValues:'%s'}" % inner,        # Bukkit PDC 字符串嵌套
    ]


def entry_model_node(entry: dict) -> dict:
    on_true = entry.get("on_true")
    if isinstance(on_true, dict):
        return on_true
    return {"type": "model", "model": entry["model"]}


def build_select(entries: list[dict], fallback) -> dict:
    return {
        "type": "minecraft:select",
        "property": "minecraft:component",
        "component": "minecraft:custom_data",
        "cases": [
            {"when": snbt_variants(e["id"]), "model": entry_model_node(e)} for e in entries
        ],
        "fallback": fallback,
    }


def build_chain(entries: list[dict], tail) -> dict:
    node = tail
    for entry in reversed(entries):
        node = {
            "type": "minecraft:condition",
            "property": "minecraft:component",
            "predicate": "minecraft:custom_data",
            "value": {"PublicBukkitValues": {"slimefun:slimefun_item": entry["id"]}},
            "on_false": node,
            "on_true": entry_model_node(entry),
        }
    return node


def pick_structure(entries: list[dict], tail) -> str:
    # 线性 condition 链每多一条就多一层嵌套，超过 Gson 上限整个文件都会解析失败
    return "chain" if len(entries) + 3 + depth(tail) <= MAX_NESTING else "select"


def render_item_definition(entries: list[dict], tail) -> tuple[dict, str]:
    structure = pick_structure(entries, tail)
    model = build_select(entries, tail) if structure == "select" else build_chain(entries, tail)
    return {"model": model}, structure


def manifest_entries(manifest: dict) -> list[dict]:
    entries = manifest.get("entries")
    return entries if isinstance(entries, list) else []


# ------------------------------------------------------------------- 菜单 UI --
def ask(prompt: str, default: str | None = None) -> str:
    if sys.stdin is None:
        raise SystemExit(
            "[x] 当前环境没有标准输入（可能由 pythonw.exe 启动，双击时常见）。\n"
            "    请在 PowerShell / cmd 里运行: python tools/add_texture.py"
        )
    while True:
        try:
            raw = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            sys.exit(1)
        except (RuntimeError, OSError) as exc:  # pythonw 下 sys.stdin 不可用
            raise SystemExit(f"[x] 无法读取输入: {exc}\n    请在终端里运行脚本。")
        raw = raw.strip().lstrip("\ufeff").strip()  # 兼容重定向输入带的 BOM
        if raw.lower() in {"q", "quit", "exit"}:
            print("已取消。")
            sys.exit(1)
        if not raw:
            if default is not None:
                return default
            print("  [!] 不能为空，请重新输入（q 退出）")
            continue
        return raw


def print_menu(title: str, options: list[str], columns: int = 1, extra: str | None = None) -> None:
    print(title)
    if columns <= 1:
        for idx, opt in enumerate(options, 1):
            print(f"  {idx:>3}) {opt}")
    else:
        width = max(len(o) for o in options) + 4
        for idx, opt in enumerate(options, 1):
            cell = f"{idx:>3}) {opt}"
            print(cell.ljust(width), end="\n" if idx % columns == 0 else "")
        if len(options) % columns:
            print()
    if extra:
        print(f"  {extra}")


def ask_index(prompt: str, options: list[str]) -> str:
    while True:
        raw = ask(prompt)
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        for opt in options:
            if opt.lower() == raw.lower():
                return opt
        print(f"  [!] 无效选择 {raw!r}：请输入 1-{len(options)} 的序号或完整名称（q 退出）")


# --------------------------------------------------------------- 导入文件扫描 --
def scan_imports() -> tuple[list[tuple[Path, str]], list[str]]:
    """返回 ([(图片路径, 规范化ID)], [问题描述])。"""
    ok: list[tuple[Path, str]] = []
    problems: list[str] = []
    seen: dict[str, Path] = {}

    files = sorted(p for p in IMPORT_DIR.rglob("*") if p.is_file() and not p.name.startswith("."))
    for path in files:
        suffix = path.suffix.lower()
        if suffix in OTHER_IMAGE_SUFFIXES:
            problems.append(f"{path.name}: 不是 PNG（{suffix}），请先另存为 .png 再导入")
            continue
        if suffix not in IMAGE_SUFFIXES:
            problems.append(f"{path.name}: 忽略非图片文件")
            continue

        stem = path.stem.strip()
        item_id = stem.upper()
        if stem != item_id:
            problems.append(f"{path.name}: 文件名已自动转为大写 -> {item_id}")
        if not ID_RE.match(item_id):
            bad = sorted({c for c in item_id if not (c.isalnum() or c == "_")})
            hint = f"，非法字符: {' '.join(repr(c) for c in bad)}" if bad else "，需以字母开头且只含 A-Z/0-9/_"
            problems.append(f"{path.name}: 物品 ID 不规范 -> {item_id}{hint}，已跳过")
            continue
        with open(path, "rb") as fh:
            head = fh.read(8)
        if head != PNG_MAGIC:
            problems.append(f"{path.name}: 文件头不是 PNG（可能已损坏或后缀被改），已跳过")
            continue
        if item_id in seen:
            problems.append(f"{path.name}: 与 {seen[item_id].name} 对应同一个 ID {item_id}，已跳过")
            continue
        seen[item_id] = path
        ok.append((path, item_id))
    return ok, problems


def default_resource_dir(pack: Pack, ns: str) -> str:
    """为该附属推荐一个资源子目录：取现有 item/generated 模型最常见的目录。"""
    counter: dict[str, int] = {}
    model_root = pack.assets / ns / "models"
    if model_root.is_dir():
        for path in model_root.rglob("*.json"):
            rel = path.relative_to(model_root).with_suffix("").as_posix()
            try:
                node = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if node.get("parent") == GENERATED_PARENT:
                parent_dir = rel.rpartition("/")[0] or "item"
                counter[parent_dir] = counter.get(parent_dir, 0) + 1
    if not counter:
        return "item"
    return max(counter.items(), key=lambda kv: (kv[1], -len(kv[0])))[0]


# ---------------------------------------------------------------------- 生成 --
class Report:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.updated: list[str] = []
        self.skipped: list[str] = []
        self.failed: list[str] = []
        self.lines: list[str] = []

    def add(self, kind: str, text: str) -> None:
        getattr(self, kind).append(text)
        self.lines.append(text)

    def note(self, text: str) -> None:
        """记一条只输出、不计入统计的信息（如清单写入）。"""
        self.lines.append(text)

    def summary(self) -> str:
        return (
            f"新增 {len(self.created)} / 更新 {len(self.updated)} / "
            f"跳过 {len(self.skipped)} / 失败 {len(self.failed)}"
        )


def generate(
    pack: Pack,
    ns: str,
    base: str,
    items: list[tuple[Path, str]],
    resource_dir: str,
    report: Report,
) -> bool:
    """把导入的图片落到附属目录并注册进清单，返回是否需要重建产物。"""
    manifest = pack.load_manifest(base)
    entries = manifest_entries(manifest)
    index = {e.get("id"): e for e in entries}
    owners = pack.id_owner_map()
    changed = False
    appended = 0

    for png, item_id in items:
        elsewhere = [b for b in owners.get(item_id, []) if b != base]
        if elsewhere:
            report.add(
                "skipped",
                f"[跳过] {item_id}: 已在基底 {', '.join(elsewhere)} 中注册，改基底不会生效；"
                f"如需换图请在对应基底上重导",
            )
            continue

        entry = index.get(item_id)
        if entry and isinstance(entry.get("model"), str):
            # 已有条目：模型路径以清单为准，贴图路径以模型 layer0 为准
            # （两者可能不同，例如 cultivation 模型在 products/、贴图在 item/）
            model_ref = entry["model"]
            tex_ref = existing_texture_ref(pack, entry)
            action = "更新"
        else:
            model_ref = tex_ref = f"{ns}:{resource_dir.strip('/')}/{item_id.lower()}"
            action = "新增"

        tex_path = pack.texture_file(tex_ref)
        tex_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(png, tex_path)

        model_path = pack.model_file(model_ref)
        model_state = "已存在"
        if not model_path.exists():
            write_json_compact(model_path, {"parent": GENERATED_PARENT, "textures": {"layer0": tex_ref}})
            model_state = "已创建"
        else:
            layer0 = read_layer0(model_path)
            if layer0 and layer0 != tex_ref:
                report.add(
                    "skipped",
                    f"[!] {item_id}: 模型 {model_ref} 的 layer0 指向 {layer0}，"
                    f"贴图已写到 {tex_path.name}，如需生效请手动改模型",
                )

        if not entry:
            entries.append({"id": item_id, "model": model_ref})
            index[item_id] = entries[-1]
            owners.setdefault(item_id, []).append(base)
            changed = True
            appended += 1

        report.add(
            "created" if action == "新增" else "updated",
            f"[{action}] {item_id}\n"
            f"       模型: {model_path.relative_to(pack.root).as_posix()}（{model_state}）\n"
            f"       贴图: {tex_path.relative_to(pack.root).as_posix()}",
        )

    if changed:
        pack.save_manifest(base, manifest)
        report.note(f"[清单] data/itemdefs/{base}.json 追加 {appended} 条（共 {len(entries)} 条）")
    return changed


def read_layer0(model_path: Path) -> str | None:
    try:
        node = json.loads(model_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    textures = node.get("textures")
    if isinstance(textures, dict):
        value = textures.get("layer0")
        if isinstance(value, str):
            return value
    return None


def existing_texture_ref(pack: Pack, entry: dict) -> str:
    """已有条目：优先用模型里的 layer0，其次用模型路径本身。"""
    ref = entry["model"]
    model_path = pack.model_file(ref)
    if model_path.exists():
        layer0 = read_layer0(model_path)
        if layer0:
            return layer0
    return ref


def rebuild_outputs(pack: Pack, base: str) -> list[str]:
    """按清单重建 assets/minecraft/items/<base>.json（player_head 含 overlay）。"""
    manifest = pack.load_manifest(base)
    if manifest.get("generated") is not True:
        return []

    entries = manifest_entries(manifest)
    logs: list[str] = []

    doc, structure = render_item_definition(entries, manifest.get("tail"))
    out = pack.items_out / f"{base}.json"
    write_json(out, doc)
    logs.append(f"{out.relative_to(pack.root).as_posix()} ({structure}, {len(entries)} 条目)")

    if base == "player_head" and manifest.get("tail_overlay") is not None:
        ov_doc, ov_structure = render_item_definition(entries, manifest["tail_overlay"])
        ov = pack.overlay_items / f"{base}.json"
        write_json(ov, ov_doc)
        logs.append(f"{ov.relative_to(pack.root).as_posix()} ({ov_structure}, {len(entries)} 条目)")
    return logs


# ------------------------------------------------------------------- 交互流程 --
def choose_addon(pack: Pack) -> str:
    addons = pack.addons()
    if not addons:
        raise SystemExit("[x] assets/ 下没有任何附属目录，无法继续")
    rows = []
    for ns in addons:
        models, tex = pack.addon_stats(ns)
        tag = "（原版命名空间）" if ns == "minecraft" else ""
        rows.append(f"{ns:<20} models {models:>5} / textures {tex:>5}{tag}")
    print_menu("\n可用附属：", rows)
    choice = ask_index("选择附属序号: ", rows)
    return addons[rows.index(choice)]


def choose_type() -> str:
    options = [
        "head  头颅 —— 注册到 player_head，作为 3D 头颅的图标",
        "block 方块 —— 需再选一个方块类原版基底（如 furnace / chest）",
        "item  物品 —— 需再选任意原版基底（如 apple / diamond_sword）",
    ]
    print_menu("\n目标类型：", options)
    choice = ask_index("选择类型序号: ", options)
    return choice.split()[0]


def generated_bases(pack: Pack) -> list[str]:
    """可自动重建的基底（generated: true）。"""
    out = []
    for base in pack.manifest_bases():
        try:
            if pack.load_manifest(base).get("generated") is True:
                out.append(base)
        except (json.JSONDecodeError, OSError):
            continue
    return out


def choose_base(pack: Pack, type_key: str) -> str:
    bases = generated_bases(pack)
    if not bases:
        raise SystemExit("[x] data/itemdefs 里没有可重建（generated: true）的基底")
    filtered = [b for b in bases if BLOCK_HINT.search(b)] if type_key == "block" else bases
    if not filtered:
        filtered = bases

    show_all = False
    while True:
        current = bases if show_all else filtered
        title = f"\n可选{'方块' if type_key == 'block' else '物品'}基底（共 {len(current)} 个）："
        print_menu(title, current, columns=3, extra="直接输入名称也可；a = 显示全部基底")
        raw = ask("选择基底: ")
        if raw.lower() == "a":
            show_all = True
            continue
        if raw.isdigit() and 1 <= int(raw) <= len(current):
            return current[int(raw) - 1]
        for base in bases:
            if base.lower() == raw.lower():
                return base
        print(f"  [!] 无效选择 {raw!r}：请输入 1-{len(current)} 的序号、基底名称，或 a 查看全部")


def ask_resource_dir(pack: Pack, ns: str) -> str:
    default = default_resource_dir(pack, ns)
    prompt = f"\n新条目资源子目录（回车使用 {default}，或输入如 {ns}/items）: "
    raw = ask(prompt, default=default)
    raw = raw.strip().strip("/")
    if ":" in raw:
        print("  [!] 不要带命名空间前缀，脚本会自动加")
        raw = raw.split(":", 1)[1].strip("/")
    return raw or default


# --------------------------------------------------------------------- verify --
def verify(pack: Pack) -> int:
    bad = 0
    checked = 0
    for base in pack.manifest_bases():
        manifest = pack.load_manifest(base)
        if manifest.get("generated") is not True:
            continue
        checked += 1
        doc, _ = render_item_definition(manifest_entries(manifest), manifest.get("tail"))
        target = pack.items_out / f"{base}.json"
        if not target.exists():
            print(f"[x] {base}: 缺少产物 {target.relative_to(pack.root).as_posix()}")
            bad += 1
            continue
        current = target.read_text(encoding="utf-8").replace("\r\n", "\n")
        expected = json.dumps(doc, indent=2, ensure_ascii=False)
        if current != expected:
            print(f"[x] {base}: 产物与清单不一致（请重新生成）")
            bad += 1
        if base == "player_head" and manifest.get("tail_overlay") is not None:
            ov_target = pack.overlay_items / "player_head.json"
            ov_doc, _ = render_item_definition(manifest_entries(manifest), manifest["tail_overlay"])
            if not ov_target.exists() or ov_target.read_text(encoding="utf-8").replace("\r\n", "\n") != json.dumps(ov_doc, indent=2, ensure_ascii=False):
                print("[x] player_head: overlay 产物与清单不一致")
                bad += 1
    print(f"verify: 检查 {checked} 个可重建基底，异常 {bad} 个")
    return 0 if bad == 0 else 1


# ----------------------------------------------------------------------- main --
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="为材质包批量导入新增材质（PNG 以 Slimefun 物品 ID 命名）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python tools/add_texture.py\n"
            "  python tools/add_texture.py --addon slimefun --type head --dir slimefun/items\n"
            "  python tools/add_texture.py --addon cultivation --type item --base apple --dir item\n"
        ),
    )
    parser.add_argument("--addon", help="附属命名空间（跳过交互选择）")
    parser.add_argument("--type", choices=["head", "block", "item"], help="目标类型")
    parser.add_argument("--base", help="原版基底（block/item 必填；head 固定为 player_head）")
    parser.add_argument("--dir", dest="resource_dir", help="新条目的资源子目录，如 slimefun/items")
    parser.add_argument("--no-rebuild", action="store_true", help="只落盘模型/贴图/清单，不重建 items/*.json")
    parser.add_argument("--verify", action="store_true", help="校验清单与产物是否一致后退出")
    parser.add_argument("--no-pause", action="store_true", help="结束后不停留（自动化/脚本调用）")
    args = parser.parse_args(argv)

    pack = Pack.locate(SCRIPT_DIR)
    print(f"材质包根目录: {pack.root}")

    if args.verify:
        return verify(pack)

    try:
        IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SystemExit(
            f"[x] 无法创建导入目录 {IMPORT_DIR}: {exc}\n"
            "    请检查该目录是否只读/被占用，或把脚本放到有写权限的目录。"
        )
    print(f"导入目录: {IMPORT_DIR}")

    items, problems = scan_imports()
    if problems:
        print("\n导入检查：")
        for line in problems:
            print(f"  [!] {line}")
    if not items:
        print(
            "\n[x] 没有可用的图片。请把 PNG 按物品 ID 命名后放入：\n"
            f"    {IMPORT_DIR}\n"
            "    例如: PORTABLE_CRAFTER.png、DYED_SMALL_BACKPACK_LIME.png\n"
            "    （文件名 = Slimefun 物品 ID，只允许 A-Z / 0-9 / 下划线）"
        )
        if _is_windows() and _has_console() and _console_is_ours():
            # 双击场景：直接把目录打开，省得用户自己找
            try:
                os.startfile(str(IMPORT_DIR))  # type: ignore[attr-defined]
                print("    已为你打开该文件夹。")
            except Exception:
                pass
        return 1
    print(f"\n识别到 {len(items)} 张待导入图片：{', '.join(i for _, i in items)}")

    # ---- 选择 ----
    ns = args.addon or choose_addon(pack)
    if ns not in pack.addons():
        raise SystemExit(f"[x] 无此附属: {ns}（可选: {', '.join(pack.addons())}）")

    type_key = args.type or choose_type()
    if type_key == "head":
        base = "player_head"
        if not pack.manifest_path(base).exists():
            raise SystemExit("[x] 缺少 data/itemdefs/player_head.json")
    else:
        base = args.base or choose_base(pack, type_key)
        if not pack.manifest_path(base).exists():
            raise SystemExit(f"[x] 无此基底: {base}")
        if pack.load_manifest(base).get("generated") is not True:
            raise SystemExit(
                f"[x] 基底 {base} 是手工维护（generated: false），无法自动注册新条目；请换一个基底"
            )

    # 只为“新条目”询问子目录；全部命中已有条目时不需要
    existing_ids = {e.get("id") for e in manifest_entries(pack.load_manifest(base))}
    packed_ids = {item_id for _, item_id in items}
    resource_dir = args.resource_dir
    if packed_ids - existing_ids and not resource_dir:
        resource_dir = ask_resource_dir(pack, ns)
    resource_dir = (resource_dir or default_resource_dir(pack, ns)).strip("/")

    # ---- 生成（不再二次确认） ----
    print(f"\n执行生成 -> 附属 {ns} / 类型 {type_key} / 基底 {base} / 子目录 {resource_dir}")
    report = Report()
    changed = generate(pack, ns, base, items, resource_dir, report)
    for line in report.lines:
        print(f"  {line}")

    if not args.no_rebuild and changed:
        print("\n重建物品定义：")
        for line in rebuild_outputs(pack, base):
            print(f"  [ok] {line}")
    elif not args.no_rebuild:
        print("\n清单无新增条目（仅更新贴图），无需重建物品定义。")
    else:
        print(f"\n[!] 已按 --no-rebuild 跳过重建；去掉该参数重跑一次才会写入 "
              f"assets/minecraft/items/{base}.json")

    print(f"\n完成：{report.summary()}")
    print("游戏内按 F3+T 重载资源（或重启客户端）即可看到变化。")
    return 0 if not report.failed else 1


def run(argv: list[str]) -> int:
    """执行主流程并兜住所有异常，保证双击时也能看到失败原因。"""
    try:
        return main(argv)
    except SystemExit as exc:  # argparse 或业务校验主动退出
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            return 1
        return int(exc.code or 0)
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130
    except Exception:
        print("\n[x] 运行出错，以下是详细信息（请截图反馈）：", file=sys.stderr)
        traceback.print_exc()
        detail = traceback.format_exc()
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write("\n==== 运行出错 ====\n" + detail)
        except OSError:
            _message_box(f"add_texture.py 运行出错：\n\n{detail[-1500:]}")
        return 1


def cli(argv: list[str] | None = None) -> int:
    """双击/终端两种入口的统一引导。"""
    argv = list(sys.argv[1:] if argv is None else argv)

    relaunched = _relaunch_in_console(argv)
    if relaunched is not None:
        return relaunched

    _harden_streams()
    if sys.stdout is None:  # 真·无输出流（pythonw 且重启失败）时镜像到日志
        _setup_log_fallback()

    try:
        return run(argv)
    finally:
        if _should_pause(argv):
            _pause()


if __name__ == "__main__":
    sys.exit(cli())
