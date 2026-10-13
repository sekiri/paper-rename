# paper-rename

論文PDFを、PDF内から読み取ったタイトルでリネームするCLIツール。

arXivの `2301.00001v1.pdf` や DOI 名 `s41586-020-2649-2.pdf` のような
判別不能なファイル名を、PDFメタデータまたは1ページ目のタイポグラフィから
抽出した論文タイトルへ変更します。

## 使い方

依存は [PyMuPDF](https://pypi.org/project/pymupdf/) のみ。グローバルに入れなくてすむ方法はいくつかあります。

```bash
# 方法1(推奨): uvならvenvすら不要。スクリプト内のPEP 723メタデータから自動で一時環境を作る
uv run paper_rename.py ~/Downloads

# 方法2: プロジェクト用venv
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install pymupdf
python paper_rename.py ~/Downloads
```

```bash
python3 paper_rename.py ~/Downloads          # 一覧表示 → y入力で実行
python3 paper_rename.py --dry-run ~/Downloads  # 一覧表示のみ
python3 paper_rename.py --apply ~/Downloads    # 確認なしで即実行
```

- 既定でフォルダ内の全PDFが対象(`--recursive` でサブフォルダも)
- 実行前に「リネーム予定(N件)」の一覧(旧名 → 新名)を表示
- 既にタイトル風の名前・PDF以外は「対象外」として理由付きでスキップ
- 何度実行しても安全(冪等)。名前の衝突は ` (2)` 連番で回避

## ファイル名の変換規則

Windowsで使えない文字は、全角ではなく**見た目が似たUnicode文字**へ変換します。

| 元 | `:` | `/` | `?` | `*` | `"` | `<` | `>` | `\|` | `\` |
|---|---|---|---|---|---|---|---|---|---|
| 変換後 | `꞉` | `⁄` | `ʔ` | `∗` | `″` | `‹` | `›` | `ǀ` | `∖` |

- Windows予約名(`CON`, `NUL` 等)に一致するタイトルは `_CON` のように接頭辞を付与
- タイトルは120字以内(単語の切れ目で切り捨て)

## タイトル抽出の仕組み

1. PDFメタデータの `title`(LaTeX残骸や "Untitled" は除外する信用判定を通す)
2. 無ければ1ページ目で最大フォントサイズの行を結合(arXivの縦書きサイドバー等、
   回転テキストとジャーナルノイズ行は除外)

## 動作環境

Python 3.8+ と [PyMuPDF](https://pypi.org/project/pymupdf/)。
Linux / macOS / Windows(NTFS)で動作するよう設計しています。

## ライセンス

MIT
