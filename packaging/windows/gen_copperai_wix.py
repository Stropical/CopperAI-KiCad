from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from xml.sax.saxutils import escape


ROOT = Path(os.environ.get("COPPERAI_INSTALL_ROOT", r"E:\ca\install"))
OUT = Path(os.environ.get("COPPERAI_WIX_OUT", r"E:\ca\msi\CopperAI.wxs"))
VERSION = os.environ.get("COPPERAI_MSI_VERSION", "0.1.12")
NS = uuid.UUID("6b0f06f9-7551-48ac-9b71-16d1189095d8")


def xml(value: str) -> str:
    return escape(value, {'"': "&quot;"})


def ident(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:24].upper()
    return f"{prefix}_{digest}"


def guid(value: str) -> str:
    return "{" + str(uuid.uuid5(NS, value)).upper() + "}"


dirs = sorted(
    [p for p in ROOT.rglob("*") if p.is_dir()],
    key=lambda p: p.relative_to(ROOT).as_posix().lower(),
)
files = sorted(
    [p for p in ROOT.rglob("*") if p.is_file()],
    key=lambda p: p.relative_to(ROOT).as_posix().lower(),
)

dir_ids: dict[Path, str] = {ROOT: "INSTALLFOLDER"}

for directory in dirs:
    dir_ids[directory] = ident("D", directory.relative_to(ROOT).as_posix())

kicad_file_id = ""
component_lines: list[str] = []

for file in files:
    rel = file.relative_to(ROOT).as_posix()
    file_id = ident("F", rel)
    comp_id = ident("C", rel)

    if rel.lower() == "bin/kicad.exe":
        kicad_file_id = file_id

    component_lines.extend(
        [
            f'      <Component Id="{comp_id}" Directory="{dir_ids[file.parent]}" Guid="{guid(rel)}">',
            f'        <File Id="{file_id}" Source="{xml(str(file))}" KeyPath="yes" />',
            "      </Component>",
        ]
    )

if not kicad_file_id:
    raise SystemExit("Could not find bin/kicad.exe in install tree")

children: dict[Path, list[Path]] = {}

for directory in dirs:
    children.setdefault(directory.parent, []).append(directory)


def emit_dir(directory: Path, indent: int = 4) -> list[str]:
    out: list[str] = []
    pad = " " * indent

    for child in children.get(directory, []):
        out.append(f'{pad}<Directory Id="{dir_ids[child]}" Name="{xml(child.name)}">')
        out.extend(emit_dir(child, indent + 2))
        out.append(f"{pad}</Directory>")

    return out


lines = [
    '<?xml version="1.0" encoding="utf-8"?>',
    '<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs">',
    f'  <Package Name="CopperAI" Manufacturer="Stropical" Version="{VERSION}" UpgradeCode="{{6B0F06F9-7551-48AC-9B71-16D1189095D8}}" Scope="perMachine">',
    '    <MajorUpgrade DowngradeErrorMessage="A newer version of CopperAI is already installed." />',
    '    <MediaTemplate EmbedCab="yes" />',
    f'    <CustomAction Id="LaunchCopperAI" FileRef="{kicad_file_id}" ExeCommand="" Execute="immediate" Return="asyncNoWait" />',
    '    <InstallExecuteSequence>',
    '      <Custom Action="LaunchCopperAI" After="InstallFinalize" Condition="NOT Installed" />',
    '    </InstallExecuteSequence>',
    '    <Feature Id="MainFeature" Title="CopperAI" Level="1">',
    '      <ComponentGroupRef Id="ProductComponents" />',
    '      <ComponentRef Id="ApplicationShortcut" />',
    "    </Feature>",
    "  </Package>",
    "  <Fragment>",
    '    <StandardDirectory Id="ProgramFiles64Folder">',
    '      <Directory Id="INSTALLFOLDER" Name="CopperAI">',
    *emit_dir(ROOT, 8),
    "      </Directory>",
    "    </StandardDirectory>",
    '    <StandardDirectory Id="ProgramMenuFolder">',
    '      <Directory Id="ApplicationProgramsFolder" Name="CopperAI" />',
    "    </StandardDirectory>",
    "  </Fragment>",
    "  <Fragment>",
    '    <ComponentGroup Id="ProductComponents">',
    *component_lines,
    "    </ComponentGroup>",
    '    <Component Id="ApplicationShortcut" Directory="ApplicationProgramsFolder" Guid="{912B78AC-1E87-4C22-B25C-E0C2B3E62957}">',
    f'      <Shortcut Id="StartMenuCopperAI" Name="CopperAI" Description="CopperAI KiCad" Target="[#{kicad_file_id}]" WorkingDirectory="D_BIN" />',
    '      <RemoveFolder Id="RemoveApplicationProgramsFolder" On="uninstall" />',
    '      <RegistryValue Root="HKLM" Key="Software\\CopperAI" Name="installed" Type="integer" Value="1" KeyPath="yes" />',
    "    </Component>",
    "  </Fragment>",
    "</Wix>",
]

text = "\n".join(lines) + "\n"
text = text.replace('WorkingDirectory="D_BIN"', f'WorkingDirectory="{dir_ids[ROOT / "bin"]}"')
OUT.write_text(text, encoding="utf-8")
print(f"Wrote {OUT} with {len(files)} files")
