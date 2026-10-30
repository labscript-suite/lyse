Introduction
==============

**Lyse** is a data analysis system which gets *your code* running on experimental data as it is acquired. It is fundamentally based around the ideas of experimental *shots* and analysis *routines*. A shot is one trial of an experiment, and a routine is a ``Python`` script, written by you, that does something with the measurement data from one or more shots.

Analysis routines can be either *single-shot* or *multi-shot*. This determines what data and functions are available to your code when it runs. A single-shot routine has access to the data from only one shot, and functions available for saving results only to the hdf5 file for that shot. A multi-shot routine has access to the entire dataset from all the runs that are currently loaded into **lyse**, and has functions available for saving results to an hdf5 file which does not belong to any of the shots---it's a file that exists only to save the "meta results".

Actually things are far less magical than that. The only enforced difference between a single shot routine and a multi-shot routine is a single variable provided to your code when **lyse** runs it: a variable in the lyse namespace called path. If you have told **lyse** that your routine is a singleshot one, then this is the path to the hdf5 file of the shot being analysed. If you've told **lyse** that your routine is a multishot one, then path is None.

Each routine runs in a subprocess of its own, which **lyse** starts when the routine is added and keeps running between shots. The routine runs with its own directory as the working directory and at the start of sys.path. Its global variables are cleared before each run, but modules that it imports stay imported, and lyse.routine_storage keeps the objects stored in it from one run to the next.

The other differences listed above are conventions only (though **lyse**'s design is based around the assumption that you'll follow these conventions most of the time), and pertain to how you use the API that **lyse** provides, which will be different depending on what sort of analysis you're doing.

The **lyse** API
~~~~~~~~~~~~~~~~~

So great, you've got a single filepath. What data analysis could you possibly do with that? It might seem like you have to still do the same amount of work that you would without an analysis system! Whilst that's not quite true, it's intentionally been designed that way so that you can run your code outside **lyse** with very little modification. Another motivating factor is to minimise the amount of magic black box behaviour, such that an analysis routine is an ordinary ``Python`` script that uses the API of **lyse**. **lyse** is both a program which executes your code, and an API that your code can call on.

To use the API in an analysis routine, begin your code with:

.. code-block:: python

	import lyse

The details of the API are found in the :doc:`API reference<api/_autosummary/lyse>`.

**lyse** GUI
~~~~~~~~~~~~~~~

The **lyse** GUI uses the API to apply single and multi-shot routines to collections of shot files, added either manually by the user or automatically by BLACS after shot completion.

Here's a screenshot of **lyse**, with two single-shot and two multi-shot routines and a sequence of fifteen shots that have been analysed:

.. _fig-gui:

.. figure:: /img/lyse_interface.png
    :alt: The lyse window with routines, analysed shots and the output of the routines

    The **lyse** GUI. The numbers match the list below.

#. Here's where single shot routines can be added and removed, with the plus and minus buttons. They will be executed in order on each shot (more on how that works shortly). They can be reordered with the arrow buttons, and enabled or disabled with the checkboxes on the left. The icon next to each routine shows whether it ran successfully on the last shot. The right-click menu restarts the subprocess of the selected routines.

#. Here is where multi-shot routines can be added or removed, in the same way as single shot routines.

#. *Pause* pauses analysis while it is pressed in. **lyse** by default will run all single-shot routines on a shot when it arrives (either from BLACS or having been manually added). After all the shots have been processed, only then will the multi-shot routines be executed. So if you load ten shots in quickly, the multi-shot routines won't run until they've all been processed by the single-shot routines. However most of the time there will be sufficient delay in between shots arriving that multi-shot routines will be executed pretty much every time a new shot arrives. If a single-shot routine raises an exception, analysis is paused, and the single-shot routines after it do not run on that shot. If a multi-shot routine raises an exception, the other multi-shot routines still run, and analysis is not paused.

#. *Abort* pauses analysis and interrupts the routines that are running.

#. If you want to re-run single-shot analyses on some shots, select them and click *Re-run selected*. They'll then be processed in order.

#. *Run* runs all the multi-shot routines at once, even if analysis is paused.

#. Adds shots with a file browser, and removes the selected shots.

#. *Columns...* chooses which columns of the shots table are shown.

#. Here is where shots appear, either having been sent by BLACS or having been added manually. There is a column for each global and each of the results (as saved by single-shot routines) present in the shots, and the icon in the first column shows whether all single-shot routines have run successfully on the shot. The data displayed here, including the columns that are hidden, represents the entirety of what is available to multi-shot routines via the API provided by **lyse**.

#. This is where the output of routines is displayed, errors in red. If you're putting print statements in your analysis code, here is where to look to see them. Likewise if there's an exception and analysis stops, look here to see why.

The *File* menu saves and loads the dataframe of the shots table, and saves and loads the configuration of **lyse**: its routines, whether analysis is paused, and the window layout.

.. sectionauthor:: Chris Billington