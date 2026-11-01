import json
import logging

import pytest
from aiohttp.test_utils import make_mocked_request
from PIL import Image

import execution
import folder_paths
from pack.prompt_variables import (
    LoadImagesFromSpreadsheet,
    LoadSpreadsheet,
    PromptVariables,
    fill,
    preview,
    spreadsheet_files,
)

TABLE = {
    "name": "shots.csv",
    "columns": ["Clothing", "Town", "Notes"],
    "rows": [["red coat", "Paris", ""], ["blue hat", "Rome", ""]],
    "numbers": [2, 3],
    "sheets": [],
}
VALUES = {"clothing": "red coat", "town": "Paris", "hair color": "brown", "café": "au lait"}


@pytest.fixture
def input_dir(tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    monkeypatch.setattr(folder_paths, "input_directory", str(input_dir))
    (input_dir / "shots.csv").write_text("Clothing,Town\nred coat,Paris\nblue hat,Rome\n")
    # Real files one level up, so "../" paths are only rejected by the containment checks.
    (tmp_path / "shots.csv").write_text("Clothing\nsecret\n")
    Image.new("RGB", (1, 1)).save(tmp_path / "a.png")
    return input_dir


def run(template, spreadsheet=None, **boxes):
    inputs = {"template": [template]} | ({"spreadsheet": [spreadsheet]} if spreadsheet else {})
    return PromptVariables.execute(**inputs, **{k: [v] for k, v in boxes.items()}).result[0]


@pytest.mark.parametrize("template,expected", [
    ("A man in [clothing] in [town]", "A man in red coat in Paris"),
    ("[Clothing] and [CLOTHING ]", "red coat and red coat"),
    ("[hair_color] / [ Hair  Color ]", "brown / brown"),
    ("[cat:dog:0.5] [a|b] [a::0.3]", "[cat:dog:0.5] [a|b] [a::0.3]"),
    (r"\[town\] [town]", "[town] Paris"),
    ("[] [ ]", "[] [ ]"),
    ("wearing [clothing", "wearing [clothing"),
    ("([clothing]:1.2)", "(red coat:1.2)"),
    ("[Café]", "au lait"),
])
def test_fill(template, expected):
    assert fill(template, VALUES, set()) == expected


def test_fill_reports_missing():
    missing = set()
    assert fill("[town] [Mood]", VALUES, missing) == "Paris "
    assert missing == {"mood"}


def test_schema_flags():
    assert PromptVariables.ACCEPT_ALL_INPUTS is True
    assert PromptVariables.INPUT_IS_LIST is True
    assert PromptVariables.OUTPUT_IS_LIST == [True]
    assert LoadImagesFromSpreadsheet.OUTPUT_IS_LIST == [True]


def test_typed_values_without_spreadsheet():
    assert run("A [x] in [town] #[row]", **{"[x]": "red", "[town]": "Rome"}) == ["A red in Rome #1"]


def test_spreadsheet_makes_one_prompt_per_row():
    prompts = run("[clothing] in [town] #[row]", TABLE, **{"[clothing]": "", "[town]": "  "})
    assert prompts == ["red coat in Paris #2", "blue hat in Rome #3"]


def test_typed_box_wins_for_every_row():
    assert run("[clothing] in [town]", TABLE, **{"[town]": "Oslo"}) == ["red coat in Oslo", "blue hat in Oslo"]


def test_column_named_row_wins():
    table = TABLE | {"columns": ["Row"], "rows": [["first"], ["second"]]}
    assert run("[row]", table) == ["first", "second"]


def test_missing_variable_is_empty_and_warns(caplog):
    with caplog.at_level(logging.WARNING):
        assert run("[mood]!", TABLE) == ["!", "!"]
    assert "[mood]" in caplog.text
    assert "Clothing, Town, Notes" in caplog.text


def test_stray_inputs_are_ignored():
    assert run("[x]", **{"[x]": "a", "[x]#1": "b", "foo": "c"}) == ["a"]


def test_template_list_pairs_with_rows():
    result = PromptVariables.execute(template=["[town] 1", "[town] 2"], spreadsheet=[TABLE]).result[0]
    assert result == ["Paris 1", "Rome 2"]


@pytest.mark.asyncio
async def test_frontend_boxes_reach_execute():
    prompt_inputs = {"template": "A [Clothing] in [town]", "[clothing]": "", "[town]": "Oslo"}
    inputs, _, v3_data = execution.get_input_data(prompt_inputs, PromptVariables, "1")
    assert inputs["[clothing]"] == [""]
    inputs["spreadsheet"] = [TABLE]
    results = await execution._async_map_node_over_list("p", "1", PromptVariables, inputs, "execute", v3_data=v3_data)
    output, _, _ = execution.get_output_from_returns(results, PromptVariables)
    assert output == [["A red coat in Oslo", "A blue hat in Oslo"]]


def test_spreadsheet_files(input_dir):
    (input_dir / "~$shots.xlsx").write_bytes(b"")
    (input_dir / ".hidden.csv").write_text("a\n1\n")
    (input_dir / "notes.txt").write_text("x")
    assert spreadsheet_files() == ["shots.csv"]


def test_load_spreadsheet(input_dir):
    assert LoadSpreadsheet.validate_inputs("shots.csv") is True
    for file in ["", "missing.csv", "../shots.csv"]:
        assert isinstance(LoadSpreadsheet.validate_inputs(file), str)

    table = LoadSpreadsheet.execute("shots.csv", "3", "").result[0]
    assert table["rows"] == [["blue hat", "Rome"]]
    assert table["numbers"] == [3]
    with pytest.raises(ValueError, match="No rows match"):
        LoadSpreadsheet.execute("shots.csv", "7", "")
    with pytest.raises(ValueError, match="not a row selection"):
        LoadSpreadsheet.execute("shots.csv", "two", "")

    before = LoadSpreadsheet.fingerprint_inputs("shots.csv", "all", "")
    (input_dir / "shots.csv").write_text("Clothing,Town\ngreen scarf,Oslo\n")
    assert LoadSpreadsheet.fingerprint_inputs("shots.csv", "all", "") != before


def test_fingerprint_follows_files_named_in_cells(input_dir):
    (input_dir / "shots.csv").write_text("Image\nb.png\n")
    Image.new("RGB", (2, 2)).save(input_dir / "b.png")
    before = LoadSpreadsheet.fingerprint_inputs("shots.csv", "all", "")
    Image.new("RGB", (3, 3)).save(input_dir / "b.png")
    assert LoadSpreadsheet.fingerprint_inputs("shots.csv", "all", "") != before


@pytest.mark.asyncio
async def test_preview(input_dir):
    response = await preview(make_mocked_request("GET", "/prompt_variables/preview?file=shots.csv&rows=3"))
    assert response.status == 200
    assert json.loads(response.text) == {
        "columns": ["Clothing", "Town"], "count": 1, "numbers": [3, 3], "first_row": ["blue hat", "Rome"], "sheets": [],
    }
    for query in ["file=../shots.csv", "file=shots.csv&rows=9", "file=shots.csv&sheet=x&rows=two"]:
        response = await preview(make_mocked_request("GET", f"/prompt_variables/preview?{query}"))
        assert response.status == 400
        assert json.loads(response.text)["error"]


def test_load_images(input_dir):
    Image.new("RGB", (4, 3), (255, 0, 0)).save(input_dir / "a.png")
    (input_dir / "faces").mkdir()
    Image.new("RGB", (2, 2)).save(input_dir / "faces" / "b.png")
    table = TABLE | {"columns": ["Image", "Town"], "rows": [["a.png", "Paris"], ["faces/b.png", "Rome"]]}

    images = LoadImagesFromSpreadsheet.execute(table, "[image]").result[0]
    assert [tuple(image.shape) for image in images] == [(1, 3, 4, 3), (1, 2, 2, 3)]

    with pytest.raises(ValueError, match="No column named"):
        LoadImagesFromSpreadsheet.execute(table, "photo")
    for cell, message in [("", "Row 3: the"), ("nope.png", "Row 3:"), ("../a.png", "Row 3:")]:
        bad = table | {"rows": [["a.png", "Paris"], [cell, "Rome"]]}
        with pytest.raises(ValueError, match=message):
            LoadImagesFromSpreadsheet.execute(bad, "image")
