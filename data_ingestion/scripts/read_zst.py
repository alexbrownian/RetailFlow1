# read_zst.py
# A small helper that reads one of the .zst dump files line by line.
# Each line in the file is one JSON record (one Reddit post or comment).
#
# You normally don't run this file directly. The other scripts import
# the read_lines() function from here so they don't repeat the same code.
#
# Why the funny decoder below? The dump files are compressed with a very
# large "window size", so we have to read them in big chunks and sometimes
# join two chunks together before the text decodes cleanly. This chunk
# logic comes from the official PushshiftDumps examples and is the safe,
# tested way to read these specific files.

import zstandard


def _read_and_decode(reader, chunk_size, max_window_size, previous_chunk=None, bytes_read=0):
    # Read one chunk of bytes and turn it into text.
    chunk = reader.read(chunk_size)
    bytes_read += chunk_size
    if previous_chunk is not None:
        chunk = previous_chunk + chunk
    try:
        return chunk.decode()
    except UnicodeDecodeError:
        # A character was split across two chunks. Read one more chunk and try again.
        if bytes_read > max_window_size:
            raise UnicodeError("Could not decode after reading %s bytes" % bytes_read)
        return _read_and_decode(reader, chunk_size, max_window_size, chunk, bytes_read)


def read_lines(file_name):
    # This is a generator: it hands back one line (one JSON record) at a time,
    # so we never load the whole giant file into memory.
    with open(file_name, "rb") as file_handle:
        buffer = ""
        decompressor = zstandard.ZstdDecompressor(max_window_size=2**31)
        reader = decompressor.stream_reader(file_handle)
        while True:
            chunk = _read_and_decode(reader, 2**27, (2**29) * 2)
            if not chunk:
                break
            lines = (buffer + chunk).split("\n")
            for line in lines[:-1]:
                yield line
            buffer = lines[-1]  # last piece may be an unfinished line; keep it for next round
        reader.close()
