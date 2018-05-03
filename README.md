# screencast-wrapper

screencast_wrapper.py is a Python 3 script that invokes ffmpeg to record
screencasts on **Linux computers**. It takes care of gathering information from the
user (capture area co-ordinates, output file name etc.) and invoking ffmpeg. The
script also takes care of stopping running captures.

Dependencies:

The following programs are needed to be present in the PATH:

  - ffmpeg
  - xdotool

It is also recommended to install a system-wide keyboard shortcut that invokes
'screencast_wrapper.py kill'. This shortcut can then be used to terminate an
ongoing screencast capture.

A screencast video that shows screencast_wrapper in action is available [here](https://vimeo.com/267598020)

(The demonstration video above was itself captured using screencast_wrapper.py
i.e. a screencast within a screencast)

The video that was captured during the demonstration is [here](https://vimeo.com/267600923)
