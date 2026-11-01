# Prompt Variables

Write your prompt once, put the parts that change in square brackets, and fill them by typing or from a spreadsheet.

```
A man wearing [clothing], standing in the middle of [town]
```

## Nodes

- **Prompt Variables**: type a template. Every `[variable]` gets its own box on the node as you type. Use **+ Add variable** to pick a spreadsheet column or name a new one. Outputs one prompt per row.
- **Load Spreadsheet**: picks a `.xlsx`, `.csv` or `.tsv` file from the ComfyUI input folder. Upload it with the button or drop the file on the node. The first row holds the variable names; every row below it is one prompt.
- **Load Images From Spreadsheet**: loads the images named in a column (files in the input folder, like `photo.png` or `faces/anna.png`), one per row, matching the prompts.

## Quick start

1. Add **Load Spreadsheet** and upload your file (a changed file with the same name is saved as `name (1).csv`). The node lists its variables, for example `[Clothing] [Town] [Mood]`.
2. Connect it to **Prompt Variables**. An empty template fills in with every column; edit it into a sentence.
3. Connect `prompt` to **CLIP Text Encode** and queue once. ComfyUI makes one image per row.

Without a spreadsheet, just type the values into the boxes.

## Rules

- A filled box wins for every row. An empty box uses the column with the same name. With neither, the variable is left empty and the node shows a warning. Empty cells stay empty.
- Names ignore case, extra spaces and `_`: `[Hair Color]`, `[hair_color]` and a `Hair Color` column match.
- **+ Add variable** won't add a variable that is already in the template. Typing the same `[name]` twice is allowed (both get the same value), and the node points it out.
- `[row]` is the row number as Excel shows it. It is handy in a second Prompt Variables node feeding `filename_prefix` on Save Image, for example `shots/[row]_[town]`.
- Brackets containing `:` or `|`, like `[cat:dog:0.5]`, are left alone. Type `\[` and `\]` for a real bracket.
- **rows** on Load Spreadsheet takes `all`, or row numbers like `2-5, 9` to try a few rows first. **sheet** (under advanced) picks an Excel tab; leave it empty for the first one.
- Every row uses the same seed.

## Spreadsheet tips

- Excel dates come through as numbers. Format the column as Text, or export as "CSV UTF-8".
- Formulas use the value Excel last saved.
- `.xls`, `.ods` and `.numbers` files: save as `.xlsx` or `.csv` first.

## Tests

```
cd ComfyUI && venv/bin/python -m pytest custom_nodes/ComfyUI-PromptVariables/tests -q
```
