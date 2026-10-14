#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = ["pymupdf"]
# ///
r"""
paper_rename.py — 論文PDFを、PDF内から読み取ったタイトルでリネームする。

使い方:
    python3 paper_rename.py <ファイル or ディレクトリ> [...]        # プレビュー(既定)
    python3 paper_rename.py --apply <ファイル or ディレクトリ> [...]  # 実際にリネーム
    python3 paper_rename.py --apply --force dir/   # タイトル風の名前でも再抽出

- タイトルは (1) PDFメタデータ、(2) 1ページ目のタイポグラフィ、の順で高精度に推定。
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

# Windows環境でのUnicode文字出力エラーを防止
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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

MAX_TITLE_LEN = 150

# Windowsの予約名(CON, NUL等)はファイル名に使えない
WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# 1ページ目から除外する「ヘッダー・ノイズ行」のパターン
NOISE_LINE = re.compile(
    r"^\s*("
    r"ar[Xx]iv[:\s]\d{4}\.\d{4,5}"
    r"|doi[:\s/]|DOI[:\s/]"
    r"|https?://|www\."
    r"|published\s+(online|:)"
    r"|received\s+|accepted\s+"
    r"|(nature|science|cell|elsevier|springer|ieee|acm|mdpi|frontiers|wiley|oxford|bmj|jama|lancet)\b"
    r"|journal|proceedings|conference|preprint|manuscript"
    r"|version\s+\d|page\s+\d"
    r"|hhs\s+public\s+access|nih\s+public\s+access|author\s+manuscript"
    r"|open\s+access|public\s+access"
    r"|^\s*(practice\s+parameter|review\s+article|original\s+(article|research)|research\s+article|clinical\s+investigation|special\s+article|case\s+report|brief\s+report|editorial|perspective|white\s+paper)\s*[:\-]?\s*$"
    r"|^\s*(学位論文|博士論文|修士論文|卒業論文|総説|原著|症例報告|特別記事|研究報告)\s*[:\-]?\s*$"
    r"|copyright\b|all\s+rights\s+reserved|©"
    r"|downloaded\s+from"
    r")",
    re.IGNORECASE,
)


def sanitize(title: str) -> str:
    """不正文字を類似文字へ変換し、ファイル名として安全な形に整える。"""
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
    """PDFメタデータのtitle/subjectが信用できるか。"""
    t = (title or "").strip()
    if len(t) < 4 or len(t) > 350:
        return False
    if t.lower() in ("untitled", "microsoft word - untitled", "cover", "contents", "template", "default"):
        return False
    # ファイル名拡張子のメタデータ (例: CJN.15341121.pdf, 臨床神経42-6_責了.indb)
    if re.search(r"\.(pdf|docx?|pptx?|xlsx?|indd|indb|pmd|eps|ai|tex)$", t, re.IGNORECASE):
        return False
    # アプリケーション接頭辞
    if re.match(r"^microsoft\s+(word|powerpoint|excel)\b", t, re.IGNORECASE):
        return False
    # 抄録・注意書き等の文章
    if re.search(r"\b(these highlights do not include|table of contents)\b", t, re.IGNORECASE):
        return False
    # LaTeX残骸 (\title{...} 等)
    if "\\" in t or re.search(r"\\[a-zA-Z]+", t):
        return False
    return True


# 「論文タイトル」ではなく「雑誌・シリーズ名・DOI」らしきメタデータを見分ける
JOURNALISH = re.compile(
    r"(学会|誌$|誌の|vol(?:ume)?\.?\s*\d|第?\s*\d+\s*[巻号]|号$|"
    r"journal|transactions|proceedings|news(?:letter)?|abstracts?|"
    r"大会|紀要|報告集|doi[\.:/]|https?://)",
    re.IGNORECASE,
)


def assemble_line(line) -> tuple[str, float]:
    """PyMuPDFのlineからテキストと文字加重平均フォントサイズを復元する。

    PyMuPDFではスペースが独立したスパンとして出力される場合があり、
    単純な.strip()での除外や結合を行うと単語間の空白が消失するため、
    スパン間の空間配置や空白文字を正しく維持して復元する。
    """
    spans = line.get("spans", [])
    if not spans:
        return "", 0.0

    line_text = ""
    last_x1 = None
    last_size = None
    weighted_size_sum = 0.0
    char_count = 0

    for s in spans:
        t = s.get("text", "")
        if not t:
            continue
        sz = s.get("size", 0.0)
        t_stripped = t.strip()
        if t_stripped:
            weighted_size_sum += sz * len(t_stripped)
            char_count += len(t_stripped)

        # スパン間の間隔を検出してスペースを補う
        if last_x1 is not None and not line_text.endswith(" ") and not t.startswith(" "):
            gap = s["bbox"][0] - last_x1
            avg_sz = (sz + (last_size or sz)) / 2.0
            if gap > avg_sz * 0.2:
                line_text += " "
        line_text += t
        last_x1 = s["bbox"][2]
        last_size = sz

    line_text = re.sub(r"\s+", " ", line_text).strip()
    avg_size = (weighted_size_sum / char_count) if char_count > 0 else (spans[0].get("size", 0.0))
    return line_text, avg_size


def is_substantive_title_candidate(text: str) -> bool:
    """ドロップキャップ、ページ番号、記号単体を排除し、タイトル候補になり得るか判定。"""
    t = text.strip()
    # 1文字、または極端に短い英数字・記号はタイトルではない(ドロップキャップ/ページ番号)
    if len(t) <= 1:
        return False
    # 数字や記号のみの行 (例: "5", "123", "---")
    if not re.search(r"[a-zA-Z\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]", t):
        return False
    # 短すぎる英字 (例: "P", "A", "Vol")
    if len(t) < 3 and not re.search(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]", t):
        return False
    return True


def extract_title_page1(doc) -> str:
    """1ページ目: フォントサイズと垂直配置からタイトルを抽出する。"""
    page = doc[0]
    d = page.get_text("dict")
    lines = []  # (y, size, text, bbox)
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            # 回転テキスト(縦書きarXivサイドバー等)は無視
            dx, dy = line.get("dir", (1.0, 0.0))
            if abs(dy) > 0.2:
                continue
            text, size = assemble_line(line)
            if not text:
                continue
            y = line["bbox"][1]
            lines.append((y, size, text, line["bbox"]))

    if not lines:
        return ""

    page_h = page.rect.height
    is_landscape = page.rect.width > page.rect.height
    max_y_ratio = 0.70 if is_landscape else 0.55

    # ページ上部〜中上部を優先。タイトルブロックが始まっている場合は後続行も追従可能
    top = [ln for ln in lines if ln[0] < page_h * max_y_ratio and not NOISE_LINE.match(ln[2])]
    if not top:
        top = [ln for ln in lines if not NOISE_LINE.match(ln[2])] or lines
    else:
        last_top_y = max(ln[0] for ln in top)
        for ln in lines:
            if ln[0] >= page_h * max_y_ratio and ln not in top and not NOISE_LINE.match(ln[2]):
                if ln[0] - last_top_y < page_h * 0.15:
                    top.append(ln)
                    last_top_y = ln[0]

    # 実質的なテキストを持つ行から最大フォントサイズを求める (ドロップキャップ・ページ番号除外)
    substantive = [ln for ln in top if is_substantive_title_candidate(ln[2])]
    if not substantive:
        return ""

    max_size = max(ln[1] for ln in substantive)

    # まず最大フォントサイズ (±0.6) の行を集める
    strict_cands = [ln for ln in substantive if ln[1] >= max_size - 0.6]
    strict_cands.sort(key=lambda ln: ln[0])

    # 連続したブロックをグループ化
    primary_lines = [strict_cands[0]]
    for ln in strict_cands[1:]:
        prev_y = primary_lines[-1][0]
        if ln[0] - prev_y > max_size * 2.5:
            break
        primary_lines.append(ln)

    current_title = " ".join(ln[2] for ln in primary_lines).strip()

    # 既に十分な長さがあり、文末が接続詞・コロン等でなければメインタイトルとして完成している
    connectors = (
        ":", "-", "―", "—", "～", "~",
        "of", "for", "and", "with", "in", "to", "on", "by",
        "からみた", "における", "に関する", "の", "について", "のための",
    )
    cjk_count = len(re.findall(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]", current_title))
    if cjk_count > 0:
        needs_expansion = cjk_count < 10 or any(current_title.endswith(c) for c in connectors)
    else:
        needs_expansion = len(current_title) < 45 or any(current_title.endswith(c) for c in connectors)

    if needs_expansion:
        # 下方向(y座標が後続)の行のみを対象に拡張
        # 欧文ポスターや表紙などの特大フォント(>=25pt)は相対閾値を緩める
        min_ratio = 0.68 if max_size >= 25.0 else 0.80
        broader_threshold = min(max_size - 1.5, max_size * min_ratio)
        candidates_below = [
            ln for ln in substantive
            if ln[0] > primary_lines[-1][0] and ln[1] >= broader_threshold
        ]
        candidates_below.sort(key=lambda ln: ln[0])
        expanded_lines = list(primary_lines)
        for ln in candidates_below:
            prev_y = expanded_lines[-1][0]
            if ln[0] - prev_y > max_size * 2.2:
                break
            expanded_lines.append(ln)
        candidate_title = " ".join(ln[2] for ln in expanded_lines).strip()
        if len(candidate_title) > len(current_title):
            current_title = candidate_title

    # 和文間の不要な空白(割付用の空隙)を除去
    current_title = re.sub(r"(?<=[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff])\s+(?=[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff])", "", current_title)
    # 英字1文字ずつのスペース区切り (例: A K I -> AKI)
    current_title = re.sub(r"\b([A-Za-z])\s+([A-Za-z])(?:\s+([A-Za-z]))?\b", lambda m: "".join(g for g in m.groups() if g), current_title)

    return current_title


def get_title(path: Path) -> str:
    try:
        doc = pymupdf.open(path)
    except Exception as e:
        print(f"  ! 開けない: {path.name} ({e})", file=sys.stderr)
        return ""
    try:
        md = doc.metadata
        meta_t = (md.get("title") or "").strip()
        meta_s = (md.get("subject") or "").strip()

        # 1ページ目のタイポグラフィから抽出
        page_t = sanitize(extract_title_page1(doc))
        page_t_valid = page_t and is_substantive_title_candidate(page_t) and not JOURNALISH.search(page_t)

        meta_t_valid = plausible_meta(meta_t) and not JOURNALISH.search(meta_t)
        meta_s_valid = plausible_meta(meta_s) and not JOURNALISH.search(meta_s)

        # 1) メタデータが極端に長い(タイトル+副題や著者情報が結合されている)場合、
        #    1ページ目から綺麗にタイトルが取れていれば1ページ目を優先
        if page_t_valid and meta_t_valid and len(meta_t) > 160:
            return page_t

        # 2) メタデータ title
        if meta_t_valid:
            return sanitize(meta_t)

        # 3) メタデータ subject
        if meta_s_valid:
            return sanitize(meta_s)

        # 4) 1ページ目タイポグラフィ
        if page_t_valid:
            return page_t

        # 5) 最終フォールバック
        fb = sanitize(meta_t) or sanitize(meta_s) or page_t
        if fb and is_substantive_title_candidate(fb):
            return fb
        return ""
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
