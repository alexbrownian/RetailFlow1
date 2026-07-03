# peek.py
# Quick sanity check: print the first few records of a downloaded .zst file
# so you can confirm the download worked and see what fields are available.
#
# How to run (from inside the data_ingestion folder):
#   python3 scripts/peek.py data/wallstreetbets_submissions.zst
#   python3 scripts/peek.py data/wallstreetbets_submissions.zst 3   <- show 3 records
#
# Step by step, this script:
#   1. takes the file path you give it (and an optional count, default 5)
#   2. reads records one at a time using read_zst.read_lines
#   3. turns each JSON line into a Python dictionary
#   4. prints it nicely, and for the very first one lists every field name

import sys
import json
from read_zst import read_lines


# 1. Read the inputs from the command line
file_path = sys.argv[1]                                  # the .zst file
how_many = int(sys.argv[2]) if len(sys.argv) > 2 else 5  # how many records to show

count = 0
for line in read_lines(file_path):
    if line == "":
        continue
    # 3. each line is JSON text -> turn it into a dictionary
    record = json.loads(line)

    # For the first record only, list the field names so you know what you can use
    if count == 0:
        print("Field names available in this file:")
        print(", ".join(sorted(record.keys())))
        print("-" * 60)

    # 4. print this record as readable, indented JSON
    print(json.dumps(record, indent=2)[:2000])
    print("-" * 60)

    count += 1
    if count >= how_many:
        break

print("Showed %d record(s) from %s" % (count, file_path))
