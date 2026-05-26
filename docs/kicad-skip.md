## kicad-skip quickstart

This project now includes a local Python virtual environment (`.venv`) with the [`kicad-skip`](https://github.com/psychogenic/kicad-skip) library installed. These notes show how to activate it, open a schematic, and explore components from the command line.

### 1. Activate the virtualenv

From the repository root:

```bash
cd /Users/ethanmarreel/Downloads/kicad-9.0.7
source .venv/bin/activate
```

You should now have `python` and `pip` on your `PATH` pointing at the local environment. You can verify that `kicad-skip` is available:

```bash
python -c "import skip; print(skip)"
```

### 2. Load a schematic

In a Python shell:

```python
import skip

schem = skip.Schematic("samp/my_schem.kicad_sch")
schem
```

`schem` is a `Schematic` object that gives you access to all symbols, wires, labels, and other entities in the KiCad `.kicad_sch` file.

### 3. Explore symbols and properties

Loop over all symbols (components):

```python
for component in schem.symbol:
    print(component)
```

Search by reference with a regular expression:

```python
matches = schem.symbol.reference_matches(r"(C|R)2[158]")
print(matches)
```

Search by value prefix and sort the results:

```python
ten_ks = sorted(schem.symbol.value_startswith("10k"))
print(ten_ks)
```

Refer to a symbol directly by its reference designator:

```python
conn = schem.symbol.J15
```

Toggle a do-not-populate flag based on whether the symbol is in the BOM:

```python
if not conn.in_bom:
    conn.dnp.value = True
```

### 4. Writing changes back to disk

After modifying the schematic in Python, write it out to a new file:

```python
schem.write("/tmp/newfile.kicad_sch")
```

You can then open `/tmp/newfile.kicad_sch` in KiCad to inspect the changes.

### 5. Using kicad-skip in scripts

For repeatable tasks (for example, mass-adding properties or generating arrays of components), place your logic in a Python script and run it from the activated virtualenv:

```bash
source .venv/bin/activate
python scripts/my_kiskip_script.py
```

Inside `scripts/my_kiskip_script.py` you can use the same patterns:

```python
import skip

schem = skip.Schematic("path/to/your_schematic.kicad_sch")
# ... modify schem ...
schem.write("path/to/your_schematic_modified.kicad_sch")
```

