from pathlib import Path

MB=1024*1024
CODE_EXT={".py",".js",".ts",".java",".c",".cpp",".h",".cs",".go",".dart",".rs",".sh",".sql",".r",".m",".php",".rb",".kt",".swift",
          ".hpp",".hh",".hxx",".cc",".cxx",".inl",".hlsl",".glsl",".shader",".cginc",".compute",".frag",".vert",
          ".y",".l",".asm",".s",".cmake",".bat",".cmd",".ps1",".lua",".pl",".scala",".kts",".mm",".asc",".alpha"}
TABULAR_EXT={".csv",".tsv",".xlsx",".xls",".ods",".fods"}
ARCHIVE_EXT={".zip",".jar",".tar",".gz",".tgz",".7z",".rar"}
BINARY_EXT={".exe", ".dll", ".so", ".bin", ".iso", ".mp3", ".wav", ".flac", ".mp4", ".mkv",
            ".avi", ".mov", ".ttf",".woff", ".pyc", ".class", ".o", ".db", ".sqlite"}|ARCHIVE_EXT

def is_archive(path:Path)->bool:
    return path.suffix.lower() in ARCHIVE_EXT
