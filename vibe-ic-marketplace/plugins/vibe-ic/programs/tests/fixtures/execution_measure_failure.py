"""Tracked finite failure wrapper: produce the real report, then return rc7."""
import runpy
import sys

sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
sys.exit(7)
