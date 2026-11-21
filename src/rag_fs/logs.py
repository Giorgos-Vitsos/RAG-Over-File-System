import logging
import sys

LINE_START="\r\033[K"#clears line for warnings

def show_warnings()->None:
    logging.basicConfig(format=LINE_START+"%(message)s",level=logging.WARNING,stream=sys.stderr)
