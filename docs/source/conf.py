# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Path setup --------------------------------------------------------------

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
#
import copy
import os
from pathlib import Path
from jinja2 import FileSystemLoader, Environment
import importlib.metadata
# -- Project information (unique to each project) -------------------------------------

project = "lyse"
copyright = "2020, labscript suite"
author = "labscript suite contributors"

# The full version, including alpha/beta/rc tags
version = importlib.metadata.version('lyse')

release = version

# HTML icons
img_path = 'img'
html_logo = img_path + "/lyse_64x64.svg"
html_favicon = img_path + "/lyse.ico"

# -- General configuration (should be identical across all projects) ------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.autosectionlabel",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "sphinx.ext.todo",
    "sphinx.ext.viewcode",
    "sphinx_rtd_theme",
    "myst_parser",
]

autodoc_typehints = 'description'
autosummary_generate = True
numfig = True
autodoc_mock_imports = ['labscript_utils']

# mock missing site packages methods
import site
mock_site_methods = {
    # Format:
    #   method name: return value
    'getusersitepackages': '',
    'getsitepackages': []
}
__fn = None
for __name, __rval in mock_site_methods.items():
    if not hasattr(site, __name):
        __fn = lambda *args, __rval=copy.deepcopy(__rval), **kwargs: __rval
        setattr(site, __name, __fn)
del __name
del __rval
del __fn

# Prefix each autosectionlabel with the name of the document it is in and a colon
autosectionlabel_prefix_document = True
myst_heading_anchors = 2

# Add any paths that contain templates here, relative to this directory.
templates_path = ['_templates']

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = []

# The suffix(es) of source filenames.
source_suffix = {
    '.rst': 'restructuredtext',
    '.md': 'markdown',
}

# The master toctree document.
master_doc = 'index'

# intersphinx allows us to link directly to other repos sphinxdocs.
# https://www.sphinx-doc.org/en/master/usage/extensions/intersphinx.html
intersphinx_mapping = {
    'python': ('https://docs.python.org/3/', None),
    'numpy': ('https://numpy.org/doc/stable/', None),
    'scipy': ('https://docs.scipy.org/doc/scipy/', None),
    'pandas': ('https://pandas.pydata.org/pandas-docs/stable/', None),
    'qtutils': ('https://qtutils.readthedocs.io/en/stable/', None),
    'pyqtgraph': (
        'https://pyqtgraph.readthedocs.io/en/latest/',
        None,
    ),  # change to stable once v0.11 is published
    'matplotlib': ('https://matplotlib.org/stable/', None),
    'h5py': ('https://docs.h5py.org/en/stable/', None),
    'pydaqmx': ('https://pythonhosted.org/PyDAQmx/', None),
    'qt': (
        'https://riverbankcomputing.com/static/Docs/PyQt5/',
        'pyqt5-modified-objects.inv',
    )  # from https://github.com/MSLNZ/msl-qt/blob/master/docs/create_pyqt_objects.py
    # under MIT License
    # TODO
    # desktop-app
    # spinapi/pynivision/etc
}

# list of all labscript suite components that have docs
labscript_suite_programs = {
    'labscript': {
        'desc': 'Expressive composition of hardware-timed experiments',
        'icon': 'labscript_32nx32n.svg',
        'type': 'lib',
    },
    'labscript-devices': {
        'desc': 'Plugin architecture for controlling experiment hardware',
        'icon': 'labscript_32nx32n.svg',
        'type': 'lib',
    },
    'labscript-utils': {
        'desc': 'Shared modules used by the *labscript suite*',
        'icon': 'labscript_32nx32n.svg',
        'type': 'lib',
    },
    'runmanager': {
        'desc': 'Graphical and remote interface to parameterized experiments',
        'icon': 'runmanager_32nx32n.svg',
        'type': 'gui',
    },
    'blacs': {
        'desc': 'Graphical interface to scientific instruments and experiment supervision',
        'icon': 'blacs_32nx32n.svg',
        'type': 'gui',
    },
    'lyse': {
        'desc': 'Online analysis of live experiment data',
        'icon': 'lyse_32nx32n.svg',
        'type': 'gui',
    },
    'runviewer': {
        'desc': 'Visualize hardware-timed experiment instructions',
        'icon': 'runviewer_32nx32n.svg',
        'type': 'gui',
    },
}

# whether to use stable or latest version
labscript_suite_doc_version = os.environ.get('READTHEDOCS_VERSION', 'latest')
if '.' in labscript_suite_doc_version:
    labscript_suite_doc_version = 'stable'
elif labscript_suite_doc_version not in ['stable', 'latest']:
    labscript_suite_doc_version = 'latest'

# add intersphinx references for each component
labscript_intersphinx_mapping = {}
for ls_prog in labscript_suite_programs:
    val = (
        'https://docs.labscriptsuite.org/projects/{}/en/{}/'.format(
            ls_prog, labscript_suite_doc_version
        ),
        None,
    )
    labscript_intersphinx_mapping[ls_prog] = val
    if ls_prog != project:
        # don't add intersphinx for current project
        # if internal links break, they can silently be filled by links to existing online docs
        # this is confusing and difficult to detect
        intersphinx_mapping[ls_prog] = val

# add intersphinx reference for the metapackage
if project != "the labscript suite":
    val = (
        'https://docs.labscriptsuite.org/en/{}/'.format(labscript_suite_doc_version),
        None,
    )
    intersphinx_mapping['labscript-suite'] = val
    labscript_intersphinx_mapping['labscript-suite'] = val

# Make `some code` equivalent to :code:`some code`
default_role = 'code'

# hide todo notes if on readthedocs and not building the latest
if os.environ.get('READTHEDOCS') and (
    os.environ.get('READTHEDOCS_VERSION') != 'latest'
    or (
        os.environ.get('READTHEDOCS_PROJECT') == project
        or os.environ.get('READTHEDOCS_PROJECT') == 'labscriptsuite'
    )
):
    todo_include_todos = False
else:
    todo_include_todos = True

# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
#
html_theme = "sphinx_rtd_theme"
html_title = "labscript suite | {project}".format(
    project=project
    if project != "labscript-suite"
    else "experiment control and automation"
)
html_short_title = "labscript suite"

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ['_static']

# Customize the html_theme
html_theme_options = {'navigation_depth': 3}

def setup(app):
    
    app.add_css_file('custom.css')

    # generate the components.rst file dynamically so it points to stable/latest
    # of subprojects correctly
    loader = FileSystemLoader(Path(__file__).resolve().parent / templates_path[0])
    env = Environment(loader=loader)
    template = env.get_template('components.rst')
    with open(Path(__file__).resolve().parent / 'components.rst', 'w') as f:
        f.write(
            template.render(
                intersphinx_mapping=labscript_intersphinx_mapping,
                programs=labscript_suite_programs,
                current_project=project,
                img_path=img_path
            )
        )

    # hooks to test docstring coverage
    app.connect('autodoc-process-docstring', doc_coverage)
    app.connect('build-finished', doc_report)


members_to_watch = ['module', 'class', 'function', 'exception', 'method', 'attribute']
doc_count = 0
undoc_count = 0
undoc_objects = []
undoc_print_objects = False


def doc_coverage(app, what, name, obj, options, lines):
    global doc_count
    global undoc_count
    global undoc_objects

    if (what in members_to_watch and len(lines) == 0):
        # blank docstring detected
        undoc_count += 1
        undoc_objects.append(name)
    else:
        doc_count += 1


def doc_report(app, exception):
    global doc_count
    global undoc_count
    global undoc_objects
    # print out report of documentation coverage
    total_docs = undoc_count + doc_count
    if total_docs != 0:
        print(f'\nAPI Doc coverage of {doc_count/total_docs:.1%}')
        if undoc_print_objects or os.environ.get('READTHEDOCS'):
            print('\nItems lacking documentation')
            print('===========================')
            print(*undoc_objects, sep='\n')
    else:
        print('No docs counted, run \'make clean\' then rebuild to get the count.')