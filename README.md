# convert_to_utf

Copy a site tree to a `.utf` sibling directory, converting CP1251 text files to
UTF-8. The source tree is **never modified**.

Typical use: prepare an UTF-8 copy of a legacy CP1251 site for charset migration.

## What it does

For every file under the source root:

| Case | Action |
| --- | --- |
| Path matches `.gitignore` (`git check-ignore --no-index`) | skipped |
| Image / binary extension (`.jpg`, `.woff`, `.pdf`, `.swf`, ...) | copied byte-identical |
| Already decodes as UTF-8 | copied byte-identical |
| Fails UTF-8 but decodes as CP1251 | re-encoded CP1251 → UTF-8 |
| Anything else (empty, unknown encoding) | copied byte-identical |

File metadata (mtime, mode) is preserved. A CSV log and a summary of counts are
written at the end.

## Requirements

- Python 3.7+
- `git` in `PATH` (used for `.gitignore` filtering)
- optional: [`chardet`](https://pypi.org/project/chardet/) — adds a detected
  encoding column hint to the log

```bash
cd py/convert_to_utf
pip install -r requirements.txt   # optional, installs chardet
```

## Configuration

Paths are resolved with this priority (first match wins):

1. **CLI flags**: `--src`, `--dst`, `--log`
2. **Environment**: `SOURCE_PATH`, `DEST_PATH`, `LOG_PATH`
3. **`.env`** file next to `convert.py` (same key names)
4. **Auto** from the script location
   `<site_root>/py/convert_to_utf/convert.py`:
   `src=<site_root>`, `dst=<site_root>.utf`, `log=<script_dir>/convert.log`

All keys are optional — whatever is omitted is derived automatically.

```bash
cp .env.example .env   # then adjust
```

`.env` (gitignored) looks like:

```bash
SOURCE_PATH="/var/www/example.ru/data/www/example.ru"
DEST_PATH="/var/www/example.ru/data/www/example.ru.utf"
# LOG_PATH="/var/www/example.ru/data/www/example.ru/py/convert_to_utf/convert.log"
```

## Usage

```bash
# full run, paths from .env / auto defaults
python3 convert.py

# dry run: no files are written to dst (the CSV log IS still written)
python3 convert.py --dry-run

# test on one folder or single file (relative to src)
python3 convert.py --folder about --dry-run

# explicit paths, override anything
python3 convert.py --src /var/www/example.ru/data/www/example.ru \
                   --dst /var/www/example.ru/data/www/example.ru.utf

# keep a separate log per test run
python3 convert.py --folder about --dry-run --log /tmp/convert.about.log
```

The script can be run from any working directory; paths are resolved from
`__file__`, not from `cwd`.

## Output

- **Destination tree** — mirror of the source: converted files in UTF-8,
  everything else a byte-identical copy.
- **CSV log** (`action,encoding,relpath,bytes`) with one row per file:
  - `convert` — CP1251 → UTF-8 re-encoded
  - `copy` — copied as-is
  - `skip-utf8` / `skip-binary` / `skip-gitignore` — copied or skipped, see table above
  - `error` — read/write failure (message in the `encoding` column)
- **Stdout summary** — `files_seen`, `convert`, `copy`, `skip-utf8`,
  `skip-binary`, `skip-gitignore`, `error` counts.
- **Exit code** — `0` on success, `1` if any file errored.

## Notes

- `--dry-run` still writes the CSV log — pass `--log /tmp/...` so a test run
  does not overwrite the log of a previous full run.
- `.env` is gitignored both in this folder and at the site root;
  `.env.example` is tracked.
