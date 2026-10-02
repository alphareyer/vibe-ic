"""Produce the real measurement, then exit nonzero for receipt fraud controls."""
import runpy
import sys

sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
sys.exit(7)
