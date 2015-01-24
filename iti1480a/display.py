#!/usr/bin/python
from iti1480a.parser import *
import sys
import time
import errno

COLOR_GREEN = '\x1b[32m'
COLOR_STRONG_GREEN = '\x1b[1;32m'
COLOR_YELLOW = '\x1b[1;33m'
COLOR_RED = '\x1b[31m'
COLOR_STRONG_RED = '\x1b[1;31m'
COLOR_BLUE = '\x1b[1;34m'
COLOR_GREY = '\x1b[1;30m'

TOKEN_COLOR = {
    TOKEN_TYPE_OUT: COLOR_BLUE,
    TOKEN_TYPE_IN: COLOR_BLUE,
    TOKEN_TYPE_SETUP: COLOR_BLUE,
    TOKEN_TYPE_SSPLIT: COLOR_BLUE,
    TOKEN_TYPE_CSPLIT: COLOR_BLUE,

    TOKEN_TYPE_ACK: COLOR_STRONG_GREEN,
    TOKEN_TYPE_NAK: COLOR_RED,
    TOKEN_TYPE_STALL: COLOR_STRONG_RED,

    TOKEN_TYPE_NYET: COLOR_YELLOW,
    TOKEN_TYPE_PING: COLOR_YELLOW,

    TOKEN_TYPE_DATA0: COLOR_GREEN,
    TOKEN_TYPE_DATA1: COLOR_GREEN,
    TOKEN_TYPE_DATA2: COLOR_GREEN,
    TOKEN_TYPE_MDATA: COLOR_GREEN,

    TOKEN_TYPE_PRE_ERR: COLOR_RED,

    TOKEN_TYPE_SOF: COLOR_GREY,
}

def hexdump(data):
    result = []
    append = result.append
    offset = 0
    while data:
        line = data[:16]
        half1 = line[:8]
        half2 = line[8:]
        data = data[16:]
        append(
            ' ' * 20
          + ('\x1b[1;30m%03x \x1b[0;36m' % offset)
          + ' '.join(['%02x' % ord(x) for x in half1] + ['  '] * (8 - len(half1)))
          + '  '
          + ' '.join(['%02x' % ord(x) for x in half2] + ['  '] * (8 - len(half2)))
          + ' '
          + ''.join(x if x.isalnum() or x == ' ' else '.' for x in line)
          + ' ' * (16 - len(line))
          + '\x1b[0m'
        )
        offset += 16
    return '\n'.join(result)

class HumanReadable(object):
    def __init__(self, write, error, verbosity):
        self._write = write
        self._error_write = error
        self._verbosity = verbosity
        self._sof_start = (None, None)
        self._sof_count = 0
        self._sof_major = None
        self._sof_minor = None
        self._last_tic = None
        noop = lambda _, __: None
        self._dispatch = {
            MESSAGE_RAW: (lambda _, x: x) if verbosity > -1 else noop,
            MESSAGE_RESET: self._reset if verbosity > -1 else noop,
            MESSAGE_TRANSACTION: self._transaction,
            MESSAGE_INCOMPLETE: lambda x, y: self._transaction(x, y, incomplete=True),
            MESSAGE_TRANSACTION_ERROR: self._error,
            MESSAGE_LS_EOP: self._ls_eop if verbosity > 2 else noop,
            MESSAGE_FS_EOP: self._fs_eop if verbosity > 2 else noop,
        }

    def _print(self, tic, printable, write):
        if tic is None:
            time = '?'
        else:
            time = tic_to_time(tic)
            if tic < self._last_tic:
                time = '\x1b[31m' + time + '\x1b[0m'
            else:
                self._last_tic = tic
        write(time + ' ' + printable + '\n')

    def _printSOFCount(self):
        sof_tic, sof_start_frame = self._sof_start
        self._print(
            sof_tic,
            TOKEN_COLOR[TOKEN_TYPE_SOF] + 'SOF (%i) %s -> %i.%i\x1b[0m' % (
                self._sof_count,
                sof_start_frame,
                self._sof_major,
                self._sof_minor,
            ), self._write)
        self._sof_count = 0

    def push(self, tic, message_type, data):
        if self._sof_count and (message_type != MESSAGE_TRANSACTION or data[0][0] != TOKEN_TYPE_SOF):
            self._printSOFCount()
        printable = self._dispatch[message_type](tic, data)
        if printable is not None:
            self._print(tic, printable, self._write)

    def _error(self, tic, data):
        self._print(tic, '\x1b[41m%r\x1b[0m' % (data, ), self._error_write)

    def _reset(self, _, data):
        return '\x1b[35mDevice reset (%s)\x1b[0m' % (short_tic_to_time(data), )

    def _ls_eop(self, _, data):
        return '\x1b[1;30mLS EOP (%s)\x1b[0m' % (short_tic_to_time(data), )

    def _fs_eop(self, _, data):
        return '\x1b[1;30mFS EOP (%s)\x1b[0m' % (short_tic_to_time(data), )

    def _transaction(self, tic, data, incomplete=False):
        if data[0][0] == TOKEN_TYPE_SOF:
            if self._verbosity < 2:
                return
            assert len(data) == 1
            try:
                frame = decode(data[0])['frame']
            except IndexError:
                return
            if frame == self._sof_major:
                self._sof_minor += 1
            else:
                self._sof_major = frame
                self._sof_minor = 0
            if self._verbosity <= 3:
                if not self._sof_count:
                    self._sof_start = (tic, '%i.%i' % (frame, self._sof_minor))
                self._sof_count += 1
                return
        if data[-1][0] == TOKEN_TYPE_NAK and self._verbosity < 1:
            return
        result = ''
        packet_data = None
        for packet in data:
            try:
                decoded = decode(packet)
            except IndexError:
                break
            result += '%s%-7s\x1b[0m ' % (TOKEN_COLOR[packet[0]], decoded['name'])
            if 'endpoint' in decoded:
                result += '@%03i.%02i ' % (decoded['address'], decoded['endpoint'])
            elif 'port' in decoded:
                # TODO: (|C|S)SPLIT transactions
                pass
            elif 'data' in decoded:
                if self._verbosity >= 0:
                    assert packet_data is None
                    packet_data = decoded['data']
                result += '%3iB ' % len(decoded['data'])
            elif 'frame' in decoded:
                frame = decoded['frame']
                result += '%4i.%i' % (frame, self._sof_minor)
        if incomplete:
            result += '\x1b[1;31m(incomplete transaction)\x1b[0m'
        if packet_data:
            result += '\n' + hexdump(packet_data)
        return result

    def stop(self):
        if self._sof_count:
            self._printSOFCount()

CHUNK_SIZE = 16 * 1024
def main():
    from optparse import OptionParser
    parser = OptionParser()
    parser.add_option('-v', '--verbose', action='count',
        default=0, help='Increase verbosity')
    parser.add_option('-q', '--quiet', action='count',
        default=0, help='Decrease verbosity')
    parser.add_option('-i', '--infile', default='-',
        help='Data source (default: stdin)')
    parser.add_option('-o', '--outfile', default='-',
        help='Data destination (default: stdout)')
    parser.add_option('-t', '--tee', help='Also write raw input to that '
        'file. Useful as tee(1) doesn\'t close its stdin when its stdout '
        'gets closed.')
    parser.add_option('-f', '--follow', action='store_true',
        help='Keep waiting for more data when reaching eof.')
    (options, args) = parser.parse_args()
    if options.infile == '-':
        read = sys.stdin.read
    else:
        read = open(options.infile, 'r').read
    if options.outfile == '-':
        write = sys.stdout.write
    else:
        write = open(options.outfile, 'w').write
    if options.tee:
        raw_write = open(options.tee, 'w').write
    else:
        raw_write = lambda x: None
    human_readable = HumanReadable(write, sys.stderr.write,
        options.verbose - options.quiet)
    stream = ReorderedStream(
        Packetiser(
            TransactionAggregator(
                human_readable,
                human_readable.push,
            ),
            human_readable.push
        )
    )
    push = stream.push
    try:
        while True:
            data = read(CHUNK_SIZE)
            raw_write(data)
            try:
                push(data)
            except ParsingDone:
                break
            if len(data) < CHUNK_SIZE:
                if options.follow:
                    time.sleep(1)
                else:
                    break
        stream.stop()
    except IOError, exc:
        if exc.errno != errno.EPIPE:
            raise
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    main()

