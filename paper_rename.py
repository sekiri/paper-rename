#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = ["pymupdf"]
# ///
"""
paper_rename.py — 論文PDFを、PDF内から読み取ったタイトルでリネームする。

使い方:
    python3 paper_rename.py <ファイル or ディレクトリ> [...]        # プレビュー(既定)
    python3 paper_rename.py --apply <ファイル or ディレクトリ> [...]  # 実際にリネーム
    python3 paper_rename.py --apply --force dir/   # タイトル風の名前でも再抽出

- タイトルは (1) PDFメタデータ、(2) 1ページ目の最大フォントサイズの行、の順で推定。
- ファイル名に使えない文字 (: / * ? " < > | \) は全角ではなく
  見た目が似たUnicode文字へ変換する。
- 既定はdry-run。--apply で実行。名前が既にタイトル風(40字以上+空白あり)の
  ファイルはスキップ(--force で強制)。
"""

import argparse
import re
import sys
from pathlib import Path

import pymupdf

# Windowsで使えない文字 → 見た目が似た文字(全角ではない)
SANITIZE_MAP = {
    ":": "\uA789",   # ꞉ MODIFIER LETTER COLON
    "/": "\u2044",   # ⁄ FRACTION SLASH
    "?": "\u0294",   # ʔ LATIN SMALL LETTER GLOTTAL STOP
    "*": "\u2217",   # ∗ ASTERISK OPERATOR
    '"': "\u2033",   # ″ DOUBLE PRIME
    "<": "\u2039",   # ‹ SINGLE LEFT-POINTING ANGLE QUOTATION MARK
    ">": "\u203A",   # › SINGLE RIGHT-POINTING ANGLE QUOTATION MARK
    "|": "\u01C0",   # ǀ LATIN LETTER DENTAL CLICK
    "\\": "\u2216",  # ∖ SET MINUS
}

MAX_TITLE_LEN = 120

# Windowsの予約名(CON, NUL等)はファイル名に使えない
WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# 1ページ目から除外する「タイトルらしき行」のパターン
NOISE_LINE = re.compile(
    r"^\s*("
    r"ar[Xx]iv[:\s]\d{4}\.\d{4,5}"
    r"|doi[:\s]|DOI:"
    r"|published\s+(online|:)"
    r"|received\s+|accepted\s+"
    r"|(nature|science|cell|elsevier|springer|ieee|acm|mdpi|frontiers)\b"
    r"|journal|proceedings|conference|preprint|manuscript"
    r"|version\s+\d|page\s+\d"
    r")",
    re.IGNORECASE,
)


def sanitize(title: str) -> str:
    """不正文字を類似文字へ変換し、ファイル名として安全な形に整える。"""
    # 制御文字・不正文字を除去/置換
    out = []
    for ch in title:
        if ch in SANITIZE_MAP:
            out.append(SANITIZE_MAP[ch])
        elif ord(ch) < 0x20 or ch == "\x7f":
            out.append(" ")
        else:
            out.append(ch)
    s = "".join(out)
    # 空白類を正規化
    s = re.sub(r"\s+", " ", s).strip()
    # Windows不允许: 末尾の . と スペース、先頭の -
    s = s.rstrip(" .")
    s = s.lstrip("-")
    # 長さ制限(単語の切れ目で切る)
    if len(s) > MAX_TITLE_LEN:
        cut = s[:MAX_TITLE_LEN]
        sp = cut.rfind(" ")
        if sp > MAX_TITLE_LEN // 2:
            cut = cut[:sp]
        s = cut.rstrip(" ,;:-")
    # Windows予約名(CON等に一致したら接頭辞を付ける)
    if s.upper().split(".")[0] in WIN_RESERVED:
        s = "_" + s
    return s


def plausible_meta(title: str) -> bool:
    """PDFメタデータのtitleが信用できるか。"""
    t = (title or "").strip()
    if len(t) < 8:
        return False
    if t.lower() in ("untitled", "microsoft word - untitled"):
        return False
    if "\\" in t:  # LaTeX残骸 (\title{...} 等)
        return False
    if re.search(r"\\[a-zA-Z]+", t):
        return False
    return True


def extract_title_page1(doc) -> str:
    """1ページ目: 最大フォントサイズの行をタイトルとして結合する。"""
    page = doc[0]
    d = page.get_text("dict")
    lines = []  # (y, size, text)
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            # 回転テキスト(縦書きarXivサイドバー等)は無視
            dx, dy = line.get("dir", (1.0, 0.0))
            if abs(dy) > 0.2:
                continue
            text = "".join(s["text"] for s in spans)
            size = max(s["size"] for s in spans)
            y = line["bbox"][1]
            lines.append((y, size, text.strip()))
    if not lines:
        return ""
    # ページ上部(上位45%)だけ見る。ノイズ行は先に除外
    page_h = page.rect.height
    top = [ln for ln in lines if ln[0] < page_h * 0.45 and not NOISE_LINE.match(ln[2])] or lines
    max_size = max(ln[1] for ln in top)
    # 最大フォント±0.5の行のみ(タイトル組)
    cand = [ln for ln in top if ln[1] >= max_size - 0.5]
    if not cand:
        return ""
    cand.sort(key=lambda ln: ln[0])
    title = " ".join(ln[2] for ln in cand)
    return title


def get_title(path: Path) -> str:
    try:
        doc = pymupdf.open(path)
    except Exception as e:
        print(f"  ! 開けない: {path.name} ({e})", file=sys.stderr)
        return ""
    try:
        meta = sanitize(doc.metadata.get("title", ""))
        if plausible_meta(doc.metadata.get("title", "")):
            return meta
        page_t = sanitize(extract_title_page1(doc))
        if page_t:
            return page_t
        return meta  # メタデータが短いだけ等の最終フォールバック
    finally:
        doc.close()


def looks_like_title(name: str) -> bool:
    """既に人間可読なタイトル名とみなす条件。"""
    stem = Path(name).stem
    return len(stem) >= 40 and " " in stem


def unique_target(folder: Path, stem: str, suffix: str, planned: set) -> Path:
    p = folder / f"{stem}{suffix}"
    n = 2
    while p.exists() or p.name in planned:
        p = folder / f"{stem} ({n}){suffix}"
        n += 1
    planned.add(p.name)
    return p


def build_plan(paths, force: bool, recursive: bool):
    """(リネーム計画 [(旧Path, 新Path)], スキップ/失敗のメッセージリスト)"""
    renames, notes = [], []
    planned = set()
    n_pdf = 0
    for p in map(Path, paths):
        if p.is_dir():
            it = p.rglob("*.pdf") if recursive else p.glob("*.pdf")
            files = sorted(it)
        elif p.is_file() and p.suffix.lower() == ".pdf":
            files = [p]
        elif p.is_file():
            continue
        else:
            notes.append(f"存在しない: {p}")
            continue
        for f in files:
            n_pdf += 1
            if not force and looks_like_title(f.name):
                notes.append(f"スキップ(既にタイトル風): {f.name}")
                continue
            title = get_title(f)
            if not title:
                notes.append(f"タイトル取得できず: {f.name}")
                continue
            if title == f.stem:
                notes.append(f"変更なし: {f.name}")
                continue
            target = unique_target(f.parent, title, f.suffix, planned)
            renames.append((f, target))
    return renames, notes, n_pdf


def print_plan(renames, notes) -> None:
    if renames:
        print(f"\nリネーム予定({len(renames)}件):")
        for i, (src, dst) in enumerate(renames, 1):
            print(f"  {i:>2}. {src.name}")
            print(f"      → {dst.name}")
    else:
        print("\nリネーム対象はありません")
    if notes:
        print("\n対象外:")
        for msg in notes:
            print(f"  - {msg}")


def execute(renames) -> None:
    print()
    for src, dst in renames:
        try:
            src.rename(dst)
            print(f"  ✔ {src.name}  →  {dst.name}")
        except OSError as e:
            print(f"  ✘ リネーム失敗 {src.name}: {e}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="フォルダ内の論文PDFをタイトルでリネーム。"
        "まず変換後名前一覧を表示し、y入力で実行します。")
    ap.add_argument("paths", nargs="+", help="PDFファイルまたはディレクトリ")
    ap.add_argument("--apply", action="store_true", help="確認なしで即実行")
    ap.add_argument("--dry-run", action="store_true", help="一覧表示のみで実行しない")
    ap.add_argument("--force", action="store_true", help="既にタイトル風の名前も再処理する")
    ap.add_argument("--recursive", action="store_true", help="ディレクトリを再帰的に走査")
    args = ap.parse_args()

    renames, notes, n_pdf = build_plan(args.paths, args.force, args.recursive)
    if n_pdf == 0:
        print("PDFが見つかりません", file=sys.stderr)
        return 1
    print_plan(renames, notes)
    if not renames:
        return 0

    if args.apply:
        execute(renames)
    elif args.dry_run:
        print("\n(dry-run: --dry-run指定のため実行しませんでした)")
    elif sys.stdin.isatty():
        ans = input(f"\n{len(renames)}件をリネームしますか? [y/N]: ").strip().lower()
        if ans in ("y", "yes"):
            execute(renames)
        else:
            print("中止しました(実行されませんでした)")
    else:
        print("\n(一覧のみ。実行するには --apply を付けるか、対話ターミナルでy入力してください)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
