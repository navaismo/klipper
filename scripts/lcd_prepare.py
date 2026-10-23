#!/usr/bin/env python3
# Inspect static LCD job data without communicating with the MCU or LCD
#
# Copyright (C) 2026 navaismo
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import argparse, json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)),
                              '..', 'klippy', 'extras'))
import lcd_status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('filename', help='path relative to Moonraker gcodes')
    parser.add_argument('--moonraker-url', default='http://127.0.0.1:7125')
    parser.add_argument('--metadata-json',
                        help='use a saved metadata response; no HTTP requests')
    parser.add_argument('--thumbnail-file',
                        help='source image for the offline metadata response')
    parser.add_argument('--gcode-file',
                        help='read embedded thumbnail from local G-code')
    parser.add_argument('--output-transfer',
                        help='write binary job payload locally; does not send')
    parser.add_argument('--output-raw16',
                        help='write the Marlin-compatible thumbnail comments')
    args = parser.parse_args()
    if (args.thumbnail_file or args.gcode_file) and not args.metadata_json:
        parser.error('Local image/G-code requires --metadata-json')
    if args.thumbnail_file and args.gcode_file:
        parser.error('Choose --thumbnail-file or --gcode-file')
    inputs = [os.path.realpath(path) for path in
              (args.metadata_json, args.thumbnail_file, args.gcode_file)
              if path]
    outputs = [os.path.realpath(path) for path in
               (args.output_transfer, args.output_raw16) if path]
    if (len(set(outputs)) != len(outputs)
        or any(path in inputs for path in outputs)):
        parser.error('Output paths must differ from inputs and each other')
    try:
        if args.metadata_json:
            with open(args.metadata_json, 'rb') as f:
                raw = f.read(lcd_status.MAX_METADATA_BYTES + 1)
            if len(raw) > lcd_status.MAX_METADATA_BYTES:
                raise lcd_status.DataError('Metadata exceeds the size limit')
            metadata = json.loads(raw.decode('utf-8'))
            if isinstance(metadata, dict) and 'error' in metadata:
                raise lcd_status.DataError('Saved response contains an error')
            if isinstance(metadata, dict) and 'result' in metadata:
                metadata = metadata['result']
            job = lcd_status.prepare_metadata(args.filename, metadata)
            if args.gcode_file:
                with open(args.gcode_file, 'rb') as f:
                    data = f.read(lcd_status.lcd_thumbnail.MAX_GCODE_BYTES)
                lcd_status.attach_gcode_thumbnail(job, data)
            elif job['thumbnail'] is not None and args.thumbnail_file:
                with open(args.thumbnail_file, 'rb') as f:
                    data = f.read(lcd_status.MAX_THUMBNAIL_BYTES + 1)
                lcd_status.attach_thumbnail(job, data)
            elif args.thumbnail_file:
                raise lcd_status.DataError('No matching thumbnail in metadata')
            else:
                job['warnings'].append('Offline thumbnail bytes not loaded')
                job['thumbnail'] = None
            lcd_status.finalize_job(job)
        else:
            job = lcd_status.MoonrakerData(args.moonraker_url).prepare(
                args.filename)
        if args.output_raw16:
            if job['thumbnail'] is None:
                raise lcd_status.DataError('No thumbnail available for RAW16')
            raw = job['thumbnail']['rgb565']['data']
            block = lcd_status.lcd_thumbnail.raw16_block(raw)
            with open(args.output_raw16, 'w') as f:
                f.write(block)
        if args.output_transfer:
            with open(args.output_transfer, 'wb') as f:
                f.write(job['transfer']['data'])
        print(json.dumps(lcd_status.job_summary(job), indent=2,
                         ensure_ascii=True, allow_nan=False))
    except (lcd_status.DataError, IOError, ValueError) as e:
        parser.exit(1, 'LCD data: %s\n' % (e,))


if __name__ == '__main__':
    main()
