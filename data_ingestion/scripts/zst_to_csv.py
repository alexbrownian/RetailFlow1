# zst_to_csv.py
# Turn one downloaded .zst dump file into a tidy .csv you can open in Excel.
#
# How to run (from inside the data_ingestion folder):
#   python3 scripts/zst_to_csv.py data/wallstreetbets_submissions.zst output/wallstreetbets_submissions.csv
#
# Step by step:
#   1. read the input path and output path from the command line
#   2. look at the first record to decide if this is SUBMISSIONS or COMMENTS
#      (submissions have a "title", comments have a "body")
#   3. pick a sensible set of columns for that type
#   4. read every record, pull out those columns, and write one CSV row each
#   5. print how many rows were written

import sys
import csv
import json
from datetime import datetime, timezone
from read_zst import read_lines


# 1. inputs
input_path = sys.argv[1]
output_path = sys.argv[2]

# Columns we keep. "created_utc" is a number of seconds; we also add a
# human-readable date column called "created_date".
SUBMISSION_COLUMNS = ["created_date", "subreddit", "author", "title", "selftext",
                      "score", "num_comments", "upvote_ratio", "id", "permalink"]
COMMENT_COLUMNS = ["created_date", "subreddit", "author", "body",
                   "score", "id", "parent_id", "link_id", "permalink"]


def get_value(record, key):
    # Safely get a field. If it's missing, return an empty string.
    if key == "created_date":
        seconds = record.get("created_utc")
        if seconds is None:
            return ""
        # turn the seconds-since-1970 number into a readable UTC date/time
        return datetime.fromtimestamp(int(seconds), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    value = record.get(key, "")
    if value is None:
        return ""
    return value


# We need to peek at the first record to choose the columns, but we also
# don't want to skip it. So we read once, remember the first record, and
# decide the type before the main loop.
rows_written = 0
columns = None

with open(output_path, "w", newline="", encoding="utf-8") as out_file:
    writer = None

    for line in read_lines(input_path):
        if line == "":
            continue
        record = json.loads(line)

        # 2 + 3. on the first record, decide the type and write the header row
        if columns is None:
            if "title" in record:
                columns = SUBMISSION_COLUMNS
            else:
                columns = COMMENT_COLUMNS
            writer = csv.writer(out_file)
            writer.writerow(columns)

        # 4. write this record's chosen columns as one CSV row
        writer.writerow([get_value(record, col) for col in columns])
        rows_written += 1

        if rows_written % 100000 == 0:
            print("...%d rows so far" % rows_written)

# 5. report
print("Done. Wrote %d rows to %s" % (rows_written, output_path))
