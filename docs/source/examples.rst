Examples
==========

An analysis on a single shot
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

	from lyse import *
	from pylab import *

	# Let's obtain our data for this shot -- globals, image attributes and
	# the results of any previously run single-shot routines:
	ser = data(path)

	# Get a global called x:
	x = ser['x']

	# Get a result saved by another single-shot analysis routine which has
	# already run. The result is called 'y', and the routine was called
	# 'some_routine':
	y = ser['some_routine','y']

	# Image attributes are also stored in this series:
	w_x2 = ser['side','absorption','OD','Gaussian_XW']

	# If we want actual measurement data, we'll have to instantiate a Run object:
	run = Run(path)

	# Obtaining a trace:
	t, mot_fluorecence = run.get_trace('mot fluorecence')

	# Now we might do some analysis on this data. Say we've written a
	# linear fit function (or we're calling some other libaries linear
	# fit function):
	m, c = linear_fit(t, mot_fluorecence)

	# We might wish to plot the fit on the trace to show whether the fit is any good:

	plot(t,mot_fluorecence,label='data')
	plot(t,m*t + x,label='linear fit')
	xlabel('time')
	ylabel('MOT flourescence')
	legend()

	# Don't call show() ! lyse will introspect what figures have been made
	# and display them once this script has finished running.  If you call
	# show() it won't find anything. lyse keeps track of figures so that new
	# figures replace old ones, rather than you getting new window popping
	# up every time your script runs.

	# We might wish to save this result so that we can compare it across
	# shots in a multishot analysis:
	run.save_result('mot loadrate', c)


An analysis on multiple shots
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

	from lyse import *
	from pylab import *

	# Let's obtain the dataframe for all of lyse's currently loaded shots:
	df = data()

	# Now let's see how the MOT load rate varies with, say a global called
	# 'detuning', which might be the detuning of the MOT beams:

	detunings = df['detuning']

	# mot load rate was saved by a routine called calculate_load_rate:

	load_rates = df['calculate_load_rate', 'mot loadrate']

	# Let's plot them against each other:

	plot(detunings, load_rates,'bo',label='data')

	# Maybe we expect a linear relationship over the range we've got:
	m, c = linear_fit(detunings, load_rates)
	# (note, not a function provided by lyse, though I'm sure we'll have
	# lots of stock functions like this available for import!)

	plot(detunings, m*detunings + c, 'ro', label='linear fit')
	legend()

	#To save this result to the output hdf5 file, we have to instantiate a
	#Sequence object:
	seq = Sequence(path, df)
	seq.save_result('detuning_loadrate_slope',c)

.. sectionauthor:: Chris Billington