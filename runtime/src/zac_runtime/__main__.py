"""`python -m zac_runtime` passthrough to the gate runner."""
import sys

from zac_runtime.gate import main

if __name__ == "__main__":
    sys.exit(main())
