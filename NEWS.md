## [2.6.0] - 2020-02-17

This release includes six bugfixes, two changes to work with newer versions of a
library, and two enhancements.

- Do not throw a version error about `pandas` v1.0

- Fix bug where lyse would fail to terminate subprocesses upon quit. Normal
  subprocesses would eventually terminate themselves, but this caused lyse to hang
  whilst closing waiting for them.
  ([PR #63](https://bitbucket.org/labscript_suite/labscript_devices/pull-requests/63))

- Increase the timeout for starting subprocesses to 30 seconds. Slow computers starting
  lyse, or when starting many subprocesses at once, would often cause timeouts which
  necessitated restarting lyse.
  ([PR #64](https://bitbucket.org/labscript_suite/labscript_devices/pull-requests/64))

- In analysis routine subprocesses, force the multiprocessing module to spawn new
  processes instead of forking. This allows analysis routines to use the multiprocessing
  module, whereas otherwise this causes crashes due to zmq not being fork-safe. This
  only affects Unix, where forking is the default behaviour of the multiprocessing
  module - on Windows this was not an issue since processes were always spawned fresh.
  ([PR #65](https://bitbucket.org/labscript_suite/labscript_devices/pull-requests/65))

- Expand the allowed datatypes that may be saved/loaded as HDF5 attributes via
  `Run.save_result()` etc to anything supported by
  `labscript_utils.properties.get/set_attribute(s)`. This means that datatypes not
  supported natively by HDF5 are JSON encoded and saved as a string with a prefix
  indicating this.
  (PRs [#66](https://bitbucket.org/labscript_suite/labscript_devices/pull-requests/66)
   and [#69](https://bitbucket.org/labscript_suite/labscript_devices/pull-requests/69))

- Fix bug where lyse's close button was unresponsive if there were no analysis routines.
  ([PR #67](https://bitbucket.org/labscript_suite/labscript_devices/pull-requests/67))

- Fix incorrect logic for updating the Qt model and dataframe in lyse - it is hoped that
  this will resolve an intermittent issue (issue #45) in which the dataframe and Qt
  model are sometimes not updated after analysis completes. If anyone sees the buggy
  behaviour again, please reopen issue #45 or a new bug report.
  ([PR #68](https://bitbucket.org/labscript_suite/labscript_devices/pull-requests/68))

- Update to no longer use the deprecated `pandas` `convert_objects()` method, in favour
  of the newer `infer_objects` method. 
  ([PR #70](https://bitbucket.org/labscript_suite/labscript_devices/pull-requests/70))

- Fix a bug where lyse could crash if loading shots with different numbers of levels in
  the hierarchy of their corresponding dataframes.
  ([PR #71](https://bitbucket.org/labscript_suite/labscript_devices/pull-requests/71))

- Allow instantiating a `Run()` object from within a function, not just at the global
  scope. The default group that results will be saved to will be the name of the file
  the `Run()` object is instantiated in.
  ([PR #72](https://bitbucket.org/labscript_suite/labscript_devices/pull-requests/72))


