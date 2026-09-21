
import sys
from app_logic import summarize

if __name__ == "__main__":
    try:
        values = [int(arg) for arg in sys.argv[1:]]
        result = summarize(values)
        print(format_summary(result))
    except ValueError as ve:
        print(f"Error: {ve}", file=sys.stderr)
        sys.exit(1)
    except TypeError as te:
        print(f"Error: {te}", file=sys.stderr)
        sys.exit(1)