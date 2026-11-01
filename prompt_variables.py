import asyncio
import hashlib
import logging
import os
import re
import unicodedata

from aiohttp import web
from typing_extensions import override

import folder_paths
import nodes
from comfy_api.latest import ComfyExtension, io

from .spreadsheet import EXTENSIONS, read_spreadsheet, select_rows

# Keep VARIABLE and normalize() in sync with js/prompt_variables.js.
# [name] is a variable unless it contains : or | (A1111 [a:b:0.5], [a|b]). \[ and \] are literal brackets.
VARIABLE = re.compile(r"\\([\[\]])|\[([^\[\]\\:|\r\n]+)\]")


def normalize(name):
    """[Hair Color], [hair_color] and a 'Hair Color ' header are the same variable."""
    return " ".join(unicodedata.normalize("NFKC", name).replace("_", " ").split()).lower()


def fill(template, values, missing):
    def replace(m):
        if m.group(1):
            return m.group(1)
        name = normalize(m.group(2))
        if not name:
            return m.group(0)
        if name not in values:
            missing.add(name)
        return values.get(name, "")
    return VARIABLE.sub(replace, template)


def pick(items, i):
    return items[min(i, len(items) - 1)]


Spreadsheet = io.Custom("PROMPT_SPREADSHEET")


def spreadsheet_files():
    input_dir = folder_paths.get_input_directory()
    os.makedirs(input_dir, exist_ok=True)
    files = folder_paths.filter_files_extensions(os.listdir(input_dir), EXTENSIONS)
    # Excel writes "~$name.xlsx" lock files next to open workbooks.
    return [f for f in files if not f.startswith(("~$", "."))]


class LoadSpreadsheet(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="PromptVariablesLoadSpreadsheet",
            display_name="Load Spreadsheet",
            category="text/prompt variables",
            search_aliases=["excel", "xlsx", "csv", "tsv", "google sheets", "sheet", "table", "load csv", "load excel", "rows"],
            description="Loads a spreadsheet (.xlsx, .csv or .tsv) from the ComfyUI input folder. "
                        "The first row holds the variable names, like clothing or town. Every row below it makes one prompt.",
            inputs=[
                io.Combo.Input("file", options=spreadsheet_files(),
                               tooltip="A spreadsheet in the ComfyUI input folder. Upload one with the button below, or drop the file on this node."),
                io.String.Input("rows", default="all",
                                tooltip='Which rows to use, by the row numbers you see in Excel: "all", or something like "2-5, 9". '
                                        'Handy for trying one row before running them all.'),
                io.String.Input("sheet", default="", advanced=True,
                                tooltip="Which tab of an Excel file to read. Leave empty to use the first tab."),
            ],
            outputs=[Spreadsheet.Output(display_name="spreadsheet")],
        )

    @classmethod
    def execute(cls, file, rows="all", sheet="") -> io.NodeOutput:
        table = read_spreadsheet(folder_paths.get_annotated_filepath(file), sheet)
        return io.NodeOutput(select_rows(table, rows))

    @classmethod
    def fingerprint_inputs(cls, file, rows="all", sheet=""):
        path = folder_paths.get_annotated_filepath(file)
        with open(path, "rb") as f:
            m = hashlib.sha256(f.read())
        # Files named in cells too, so Load Images From Spreadsheet reloads an image replaced under the same name.
        for cell in sorted({cell for row in read_spreadsheet(path, sheet)["rows"] for cell in row if cell}):
            if folder_paths.exists_annotated_filepath(cell):
                stat = os.stat(folder_paths.get_annotated_filepath(cell))
                m.update(f"{cell}:{stat.st_mtime_ns}:{stat.st_size}".encode())
        return m.hexdigest()

    @classmethod
    def validate_inputs(cls, file):
        if not file:
            return "Pick a spreadsheet first, or upload one."
        if not folder_paths.exists_annotated_filepath(file):
            return f'"{file}" is not in the ComfyUI input folder.'
        return True


class PromptVariables(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="PromptVariables",
            display_name="Prompt Variables",
            category="text/prompt variables",
            search_aliases=["prompt template", "template", "variables", "fill in the blanks", "placeholders", "batch prompts",
                            "prompt list", "mail merge", "csv prompts", "excel prompts", "spreadsheet prompts", "replace words"],
            description="Write your prompt once and put the parts that change in square brackets, like [clothing] or [town]. "
                        "Each one gets its own box. Connect a spreadsheet to make one prompt per row: "
                        "empty boxes are filled from the column with the same name.",
            inputs=[
                io.String.Input("template", multiline=True, dynamic_prompts=True,
                                placeholder="A man wearing [clothing], standing in the middle of [town]",
                                tooltip="Your prompt. Words in [square brackets] become variables. "
                                        r"Type \[ for a real bracket. Brackets with : or |, like [cat:dog:0.5], are left as they are."),
                Spreadsheet.Input("spreadsheet", optional=True,
                                  tooltip="Optional. Connect Load Spreadsheet to make one prompt per row."),
            ],
            outputs=[
                io.String.Output(display_name="prompt", is_output_list=True,
                                 tooltip="One prompt per row. The nodes after this run once for each prompt."),
            ],
            # The variable boxes are added by js/prompt_variables.js, keyed "[name]".
            accept_all_inputs=True,
            # Lists in, so a wired list pairs item i with row i instead of repeating the whole sheet per item.
            is_input_list=True,
        )

    @classmethod
    def execute(cls, template, spreadsheet=None, **kwargs) -> io.NodeOutput:
        table = spreadsheet[0] if spreadsheet else {"columns": [], "rows": [[]], "numbers": [1]}
        columns = {}
        for i, name in enumerate(table["columns"]):
            columns.setdefault(normalize(name), i)
        boxes = {normalize(k[1:-1]): v for k, v in kwargs.items() if k.startswith("[") and k.endswith("]")}

        count = max(len(template), len(table["rows"]), *(len(v) for v in boxes.values()))
        missing = set()
        prompts = []
        for i in range(count):
            row = pick(table["rows"], i)
            values = {"row": str(pick(table["numbers"], i))}
            values |= {name: row[index] for name, index in columns.items()}
            values |= {name: str(pick(v, i)) for name, v in boxes.items() if str(pick(v, i)).strip()}
            prompts.append(fill(pick(template, i), values, missing))

        if missing:
            names = " ".join(f"[{name}]" for name in sorted(missing))
            hint = f" or add a column for it (columns: {', '.join(table['columns'])})" if spreadsheet else ""
            logging.warning(f"Prompt Variables: no value for {names}, left empty. Type a value in its box{hint}.")
        return io.NodeOutput(prompts)


class LoadImagesFromSpreadsheet(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="PromptVariablesLoadImages",
            display_name="Load Images From Spreadsheet",
            category="text/prompt variables",
            search_aliases=["images from csv", "images from excel", "image list", "batch images", "load images"],
            description="Loads one image per spreadsheet row from the ComfyUI input folder, using the file names in the column you pick. "
                        "Image 1 goes with prompt 1, image 2 with prompt 2, and so on.",
            inputs=[
                Spreadsheet.Input("spreadsheet"),
                io.String.Input("column", default="image",
                                tooltip="The column that holds the image file names, like photo.png or faces/anna.png."),
            ],
            outputs=[
                io.Image.Output(display_name="images", is_output_list=True, tooltip="One image per row, in row order."),
            ],
        )

    @classmethod
    def execute(cls, spreadsheet, column) -> io.NodeOutput:
        headers = [normalize(c) for c in spreadsheet["columns"]]
        name = normalize(column.strip().removeprefix("[").removesuffix("]"))
        if name not in headers:
            raise ValueError(f'No column named "{column}". Columns: {", ".join(spreadsheet["columns"])}.')
        index = headers.index(name)

        loader = nodes.LoadImage()
        images = []
        for number, row in zip(spreadsheet["numbers"], spreadsheet["rows"]):
            file = row[index]
            if not file:
                raise ValueError(f'Row {number}: the "{column}" cell is empty.')
            if not folder_paths.exists_annotated_filepath(file):
                raise ValueError(f'Row {number}: "{file}" is not in the ComfyUI input folder.')
            images.append(loader.load_image(file)[0])
        return io.NodeOutput(images)


async def preview(request):
    query = request.rel_url.query
    try:
        path = folder_paths.get_annotated_filepath(query.get("file", ""))
        table = await asyncio.to_thread(read_spreadsheet, path, query.get("sheet", ""))
        table = select_rows(table, query.get("rows", "all"))
    except (ValueError, OSError) as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response({
        "columns": table["columns"],
        "count": len(table["rows"]),
        "numbers": [table["numbers"][0], table["numbers"][-1]],
        "first_row": table["rows"][0],
        "sheets": table["sheets"],
    })


class PromptVariablesExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [LoadSpreadsheet, PromptVariables, LoadImagesFromSpreadsheet]
