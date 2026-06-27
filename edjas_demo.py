import json
import sys

from edjas.read_params import read_file

if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit("Requires spreadsheet arguments")
    if len(sys.argv) > 3:
        sys.exit("Arguments: path | path range_name")
    data = read_file(*sys.argv[1:])
    json.dump(data, sys.stdout, indent=4)
