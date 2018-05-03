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

# ------------------------------------------------------------------------------

"""pid file management library

Programs create .pid files (typically /var/run/user/<uid>/<program-name>.pid)
to keep track of the PIDs of running instances of the program. One of the uses
is to allow long-running programs or daemons to be killed using a script that
reads the .pid file.

This module provides basic management for .pid files.

Example usage:
=============

Part 1 (main program):
---------------------

      from pidfile import PidFile

      def main():
          global pid
          pid = PidFile('my-name') # Use "/var/run/user/1000/my-name.pid"
          pid.add()                # Add this instance's PID

          global get_out
          get_out = False

          signal.signal(signal.SIGUSR1, my_usr1_handler) # listen to SIGUSR1

          while not get_out:       # main loop runs forever until SIGUSR1
              do_some_work()

          # We must have got SIGUSR1
          sys.exit(0)

      def my_usr1_handler():
          global get_out
          get_out = True

      @atexit.register
      def goodbye():
          pid.remove()             # Remove this instance's PID


 Part 2 (program that kills the *last* running instance of 'my-name'):
 --------------------------------------------------------------------

      from pidfile import PidFile

      def main():
          pid = PidFile('my-name')
          pid_of_last_instance = p.last()
          if not pid_of_last_instance:
              print('No running instances of \'my-name\'')
          else:
              print('Killing {} ...'.format(pid_of_last_instance))
              os.kill(pid_of_last_instance, signal.SIGUSR1) # send SIGUSR1

 Restrictions:
 ------------
 Linux only
"""

import os
import re
import sys
import time
import fcntl

def serialize(func):
    """Decorator function to provide exclusive access to the pid file"""

    def _serialize(pid_file_instance, *args, **kwargs):
        file_name = pid_file_instance.pid_file_name()

        # If the file doesn't exist yet, don't bother trying to lock it
        try:
            fdl = open(file_name, 'r')
        except FileNotFoundError:
            ret = func(pid_file_instance, *args, **kwargs)
            return ret

        # We don't expect pid files to be locked for too long, so if we fail to
        # get the lock after a couple of seconds, something is wrong
        retry = 0
        while True:
            try:
                fcntl.flock(fdl, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                retry += 1
                if retry == 3:
                    raise
                else:
                    time.sleep(1)

        ret = func(pid_file_instance, *args, **kwargs)

        fcntl.flock(fdl, fcntl.LOCK_UN)
        fdl.close()

        return ret

    return _serialize

class PidFile(object):
    """pid file management class"""

    def __init__(self, program_name):
        """Constructor; needs the name of the program to manage.
        """
        self.program_name = program_name

    @serialize
    def add(self):
        """Add (append) pid of current process to pid file.
        Raises ValueError if current process pid already exists in pid file.
        Returns the pid, in case the caller wants to use it for debugging
        purposes.
        """
        current_pid = os.getpid()
        pid_list = self.__get_pid_list()

        for pid in pid_list:
            if pid == current_pid:
                raise ValueError('{} already in {}'.
                                 format(current_pid, self.pid_file_name()))

        pid_list.append(current_pid)
        self.__write_pid_list(pid_list)
        return current_pid

    @serialize
    def remove(self):
        """Remove the pid of the current process from the pid file.
        """
        current_pid = os.getpid()
        pid_list = self.__get_pid_list()
        pid_list.remove(current_pid)
        self.__write_pid_list(pid_list)

    @serialize
    def sanitize_pid_file(self):
        """Check the pid file and delete pids that no longer exist, don't
        belong to this user, or whose process names are different from
        this process' name
        """
        valid_list = []
        for p in self.__get_pid_list():
            (stale, reason) = self.__stale_pid(p)
            if stale:
                print('Removing {} from pid file, reason = {}'.
                      format(p, reason), file=sys.stderr, flush=True)
            else:
                valid_list.append(p)

        self.__write_pid_list(valid_list)

    @serialize
    def last(self):
        """Get the last (i.e. latest) pid from the pid file. This is expected to
        be called from 'kill' scripts to kill the last started instance of the
        program (e.g. by sending a signal to the pid returned by this method).
        Returns None if the pid file is empty
        """
        pid_list = self.__get_pid_list()
        if not pid_list:
            return None
        return pid_list[-1]

    def pid_file_name(self):
        """Returns the name of the pid file for the program
        """
        return '/var/run/user/{}/{}.pid'.format(os.getuid(),
                                                self.program_name)

    def __get_pid_list(self):
        """Open pid file, gather all the pids therein, return as a list of pid
        numbers (ints, not strings)
        Return an empty list if file does not exist
        """
        pid_list = []

        try:
            with open(self.pid_file_name()) as fd:
                for entry in fd:
                    pid_list.append(int(entry))
        except FileNotFoundError:
            pass

        return pid_list

    def __write_pid_list(self, pid_list):
        """Write the pids in the list to the pid file after first emptying it
        """
        with open(self.pid_file_name(), "w") as fd:
            for pid in pid_list:
                fd.write('{}\n'.format(pid))

    def __process_name(self, pid):
        """Helper to get the name of a process, given its pid
        """
        with open('/proc/{}/comm'.format(pid)) as fd:
            return fd.readline()

    def __process_real_uid(self, pid):
        """Get the real uid of a process, given its pid
        """
        re_uid = re.compile(r'Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+).*$')
        uid = -1
        with open('/proc/{}/status'.format(pid)) as fd:
            while True:
                line = fd.readline()
                mo = re_uid.search(line)
                if not mo:
                    continue
                uid = int(mo.group(1))
                break
        if uid == -1:
            raise ValueError('Unable to obtain UID for process {}'.format(pid))
        return uid

    def __stale_pid(self, pid):
        """Returns True if any of the three following conditions are true:
        (1) pid is not a valid process id
        (2) The process exists but its name is not the same as self.program_name
        (3) The real user id of the process is not the same as that of this
            process
        """

        if not os.path.exists('/proc/{}'.format(pid)):
            return (True, 'not running')

        our_name = self.__process_name(os.getpid())
        p_name = self.__process_name(pid)
        if p_name != our_name:
            return (True,
                    'name mismatch ({}, expected {})'.format(p_name, our_name))

        our_uid = self.__process_real_uid(os.getpid())
        p_uid = self.__process_real_uid(pid)
        if p_uid != our_uid:
            return (True,
                    'uid mismatch ({}, expected {})'.format(p_uid, our_uid))

        # Still here
        return (False, '')
