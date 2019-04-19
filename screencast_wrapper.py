#!/usr/bin/env python3

# MIT License

# Copyright (c) 2018 vnetman@zoho.com

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""screencast_wrapper.py is a Python 3 script that invokes ffmpeg to record
screencasts on Linux desktops. It takes care of gathering information from the
user (capture area co-ordinates, output file name etc.) and invoking ffmpeg. The
script also takes care of stopping running captures.

Dependencies:

The following programs are needed to be present in the PATH:

  - ffmpeg
  - xdotool

It is also recommended to install a system-wide keyboard shortcut that invokes
'screencast_wrapper.py kill'. This shortcut can then be used to terminate an
ongoing screencast capture.
"""

import argparse
import shutil
import subprocess
import re
import sys
import os
import fcntl
import time
import select
import signal
import pidfile

PROGRAM_NAME = 'screencast_wrapper'

def ffmpeg_command(top_left, bottom_right, out_file, mute):
    """Return the ffmpeg command to be executed as a list
    """
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise ValueError('This program relies on ffmpeg to do the actual '
                         'capturing, but ffmpeg appears to be missing on '
                         'this system. Please install.')

    disp = os.environ['DISPLAY']
    
    width = bottom_right['x'] - top_left['x'] + 1
    height = bottom_right['y'] - top_left['y'] + 1

    cmd_line = [ffmpeg, '-f', 'x11grab', '-r', '10',
                '-s', '{}x{}'.format(width, height),
                '-i', '{}.0+{},{}'.format(disp, top_left['x'], top_left['y'])]

    if not mute:
        cmd_line.extend(['-f', 'pulse', '-ac', '2', '-i', 'default'])

    cmd_line.append(out_file)

    print('*** Running "{}" ***'.format(cmd_line))

    return cmd_line
# -----------------------------------------------------------------------------

def ffmpeg_capture(top_left, bottom_right, out_file, mute):
    """Start ffmpeg with the appropriate parameters, and quit it on receipt of
    SIGUSR1
    """
    stop_recording = False

    def sigusr1_handler(sig, dummy):
        """SIGUSR1 handler
        Set a flag that the main loop reads and exits
        """
        assert sig == signal.SIGUSR1, "unexpected signal in handler"
        nonlocal stop_recording
        stop_recording = True

    # The 'kill' script kills us by sending us a SIGUSR1. Set up the listener
    # for that signal.
    signal.signal(signal.SIGUSR1, sigusr1_handler)

    # pid file logic
    pid = pidfile.PidFile(PROGRAM_NAME)
    pid.add()

    try:
        pipe = subprocess.Popen(ffmpeg_command(top_left, bottom_right,
                                               out_file, mute),
                                stdout=subprocess.PIPE,
                                stdin=subprocess.PIPE,
                                stderr=subprocess.PIPE)

        # Set the pipe's stdout and stderr fd's to non-blocking
        for fdesc in [pipe.stdout, pipe.stderr]:
            flags = fcntl.fcntl(fdesc, fcntl.F_GETFL)
            fcntl.fcntl(fdesc, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    except Exception as exception:
        # In case of any trouble, remove the pid file entry
        pid.remove()
        print('Failed to start ffmpeg: {}'.format(exception))
        raise

    # ffmpeg will run until continuously until we stop it. If it stops on its
    # own, that means something went wrong
    while True:
        if pipe.poll():
            print('ffmpeg has exited', flush=True)
            pid.remove()
            break

        # Continuously send ffmpeg's stdout and stderr to our stdout.
        while True:
            (readables, _, _) = select.select([pipe.stdout, pipe.stderr],
                                              [], [], 0)
            if not readables:
                break

            for fdesc in readables:
                if stop_recording:
                    break
                buf = fdesc.read(4096)
                if buf:
                    print('{}'.format(buf.decode('utf-8')), end='',
                          flush=True)

            if stop_recording:
                break

        if stop_recording:
            print('')
            print('Terminating...', end='', flush=True)
            pipe.communicate(input='q'.encode('utf-8'), timeout=10)
            pipe.wait(timeout=10)
            pid.remove()
            print('ok')
            return
# -----------------------------------------------------------------------------

def get_mouse_coordinates():
    """Run the X windows utility xdotool to get the current co-ordinates of the
    mouse pointer
    """

    xdotool = shutil.which('xdotool')
    if not xdotool:
        raise ValueError('xdotool missing on this system; please install')

    pipe = subprocess.Popen([xdotool, 'getmouselocation'],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    try:
        (out, err) = pipe.communicate(timeout=4)
    except subprocess.TimeoutExpired:
        pipe.kill()
        pipe.communicate()
        raise ValueError('Error getting mouse co-ordinates from xdotool')

    if pipe.returncode != 0:
        raise ValueError('xdotool terminated abnormally, '
                         'stderr = "{}"'.format(err.decode('utf-8')))

    xdotool_op = out.decode('utf-8')
    mat = re.search(r'^x:(\d+) +y:(\d+) .*$', xdotool_op)
    if not mat:
        raise ValueError('xdotool o/p "{}" does not conform '
                         'to expected pattern'.format(xdotool_op))

    position = dict()
    position['x'] = int(mat.group(1))
    position['y'] = int(mat.group(2))

    return position
# -----------------------------------------------------------------------------

def valid_capture_area(top_left, bottom_right):
    """Check the capture area extents for sanity.
    """
    tl_x = top_left['x']
    tl_y = top_left['y']
    br_x = bottom_right['x']
    br_y = bottom_right['y']

    if (br_x <= tl_x) or (br_y <= tl_y):
        print('The capture area ({},{}) ({},{}) '
              'is invalid.'.format(tl_x, tl_y, br_x, br_y),
              file=sys.stderr)
        return False

    print('Capture area: ({},{}) ({},{})'.format(tl_x, tl_y, br_x, br_y))
    return True
# -----------------------------------------------------------------------------

def setup_and_start_capture(out_file, mute):
    """Entry point for the 'capture' mode operation.
    """

    if 'DISPLAY' not in os.environ:
        print('DISPLAY not set; are you not in an X11 session?',
              file=sys.stderr)
        sys.exit(-1)

    if not re.search(r'.*\.[mM][kK][vV]$', out_file):
        print('Output file name needs to have a ".mkv" extension',
              file=sys.stderr)
        sys.exit(-1)

    if os.path.isfile(out_file):
        print('Output file "{}" already exists; '
              'won\'t overwrite'.format(out_file), file=sys.stderr)
        sys.exit(-1)


    print('Will write to {}'.format(out_file))

    print('-----------------------------------------------------------------')
    print('Place the mouse pointer at the TOP LEFT of the capture area.')
    print('Do *NOT* click.')
    print('Press <Enter> when ready: ', flush=True, end='')
    input()

    top_left = get_mouse_coordinates()

    print('-----------------------------------------------------------------')
    print('Place the mouse pointer at the BOTTOM RIGHT of the capture area.')
    print('Do *NOT* click.')
    print('Press <Enter> when ready: ', flush=True, end='')
    input()

    bottom_right = get_mouse_coordinates()

    print('-----------------------------------------------------------------')

    if not valid_capture_area(top_left, bottom_right):
        sys.exit(-1)

    print('-----------------------------------------------------------------')
    print('*** Ready to capture ***')
    print('Capturing will start 5 seconds after you hit <Enter>: ',
          end='', flush=True)
    input()

    time.sleep(5)

    #enhancement: play a sound that says "Action!!"

    ffmpeg_capture(top_left, bottom_right, out_file, mute)

    sys.exit(0)
# -----------------------------------------------------------------------------

def kill_last_capture():
    """'kill' mode operation. Use the pidfile to locate the pid of the last
    running instance of screencast_wrapper, and send SIGUSR1 to it
    """
    pid = pidfile.PidFile(PROGRAM_NAME)

    # Remove entries for stale instances that didn't terminate cleanly
    pid.sanitize_pid_file()

    # PID of the (most recently started) running instance
    pid_of_last_instance = pid.last()
    if not pid_of_last_instance:
        print('No running instance of {} to kill'.format(PROGRAM_NAME))
    else:
        print('Killing {} ...'.format(pid_of_last_instance))
        os.kill(pid_of_last_instance, signal.SIGUSR1) # send SIGUSR1
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()

    sub_parser = parser.add_subparsers(help='program operation', dest='opcode')
    sub_parser.required = True

    capture_parser = sub_parser.add_parser('capture',
                                           help='Capture a screencast')
    sub_parser.add_parser('kill', help='Kill the last running '
                          'capture instance')

    capture_parser.add_argument('--out', required=True, metavar='<file>',
                                help='Output file (.mkv)')
    capture_parser.add_argument('--mute', action='store_true',
                                help='Don\'t record audio')

    args = parser.parse_args()

    if args.opcode == 'capture':
        setup_and_start_capture(args.out, args.mute)
    elif args.opcode == 'kill':
        kill_last_capture()
    else:
        raise ValueError('Operation not known')
# -----------------------------------------------------------------------------

if __name__ == '__main__':
    main()
