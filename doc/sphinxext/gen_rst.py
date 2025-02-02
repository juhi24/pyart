"""
Example generation for Py-ART

Generate the rst files for the examples by iterating over the Python
example files.

Files that generate images should state with 'plot'

"""

# This files was adapted from the scikit-learn file of the same name:
# https://github.com/scikit-learn/scikit-learn/blob/master/doc/sphinxext/gen_rst.py
#
# The scikit-learn project uses this following License which applies to this
# file.
#
# New BSD License
#
# Copyright (c) 2007-2013 The scikit-learn developers.
# All rights reserved.
#
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   a. Redistributions of source code must retain the above copyright notice,
#      this list of conditions and the following disclaimer.
#   b. Redistributions in binary form must reproduce the above copyright
#      notice, this list of conditions and the following disclaimer in the
#      documentation and/or other materials provided with the distribution.
#   c. Neither the name of the Scikit-learn Developers nor the names of
#      its contributors may be used to endorse or promote products
#      derived from this software without specific prior written
#      permission.
#
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE REGENTS OR CONTRIBUTORS BE LIABLE FOR
# ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
# OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH
# DAMAGE.

from __future__ import division, print_function

from time import time
import os
import re
import shutil
import traceback
import glob
import sys
import gzip
import posixpath

# Try Python 2 first, otherwise load from Python 3
try:
    from StringIO import StringIO
    import cPickle as pickle
    import urllib2 as urllib
    from urllib2 import HTTPError, URLError
except:
    from io import StringIO
    import pickle
    import urllib.request
    import urllib.error
    import urllib.parse
    from urllib.error import HTTPError, URLError

try:
    # Python 2 built-in
    execfile
except NameError:
    def execfile(filename, global_vars=None, local_vars=None):
        with open(filename, encoding='utf-8') as f:
            code = compile(f.read(), filename, 'exec')
            exec(code, global_vars, local_vars)

try:
    basestring
except NameError:
    basestring = str

try:
    from PIL import Image
except:
    import Image

import matplotlib
matplotlib.use('Agg')

import token
import tokenize
import numpy as np


###############################################################################
# A tee object to redict streams to multiple outputs

class Tee(object):

    def __init__(self, file1, file2):
        self.file1 = file1
        self.file2 = file2

    def write(self, data):
        self.file1.write(data)
        self.file2.write(data)

    def flush(self):
        self.file1.flush()
        self.file2.flush()

###############################################################################
# Documentation link resolver objects


def get_data(url):
    """Helper function to get data over http or from a local file"""
    if url.startswith('http://'):
        resp = urllib.urlopen(url)
        encoding = resp.headers.dict.get('content-encoding', 'plain')
        data = resp.read()
        if encoding == 'plain':
            pass
        elif encoding == 'gzip':
            data = StringIO(data)
            data = gzip.GzipFile(fileobj=data).read()
        else:
            raise RuntimeError('unknown encoding')
    else:
        with open(url, 'r') as fid:
            data = fid.read()
        fid.close()

    return data


def parse_sphinx_searchindex(searchindex):
    """Parse a Sphinx search index

    Parameters
    ----------
    searchindex : str
        The Sphinx search index (contents of searchindex.js)

    Returns
    -------
    filenames : list of str
        The file names parsed from the search index.
    objects : dict
        The objects parsed from the search index.
    """
    def _select_block(str_in, start_tag, end_tag):
        """Select first block delimited by start_tag and end_tag"""
        start_pos = str_in.find(start_tag)
        if start_pos < 0:
            raise ValueError('start_tag not found')
        depth = 0
        for pos in range(start_pos, len(str_in)):
            if str_in[pos] == start_tag:
                depth += 1
            elif str_in[pos] == end_tag:
                depth -= 1

            if depth == 0:
                break
        sel = str_in[start_pos + 1:pos]
        return sel

    def _parse_dict_recursive(dict_str):
        """Parse a dictionary from the search index"""
        dict_out = dict()
        pos_last = 0
        pos = dict_str.find(':')
        while pos >= 0:
            key = dict_str[pos_last:pos]
            if dict_str[pos + 1] == '[':
                # value is a list
                pos_tmp = dict_str.find(']', pos + 1)
                if pos_tmp < 0:
                    raise RuntimeError('error when parsing dict')
                value = dict_str[pos + 2: pos_tmp].split(',')
                # try to convert elements to int
                for i in range(len(value)):
                    try:
                        value[i] = int(value[i])
                    except ValueError:
                        pass
            elif dict_str[pos + 1] == '{':
                # value is another dictionary
                subdict_str = _select_block(dict_str[pos:], '{', '}')
                value = _parse_dict_recursive(subdict_str)
                pos_tmp = pos + len(subdict_str)
            else:
                raise ValueError('error when parsing dict: unknown elem')

            key = key.strip('"')
            if len(key) > 0:
                dict_out[key] = value

            pos_last = dict_str.find(',', pos_tmp)
            if pos_last < 0:
                break
            pos_last += 1
            pos = dict_str.find(':', pos_last)

        return dict_out

    # parse objects
    query = 'objects:'
    pos = searchindex.find(query)
    if pos < 0:
        raise ValueError('"objects:" not found in search index')

    sel = _select_block(searchindex[pos:], '{', '}')
    objects = _parse_dict_recursive(sel)

    # parse filenames
    query = 'filenames:'
    pos = searchindex.find(query)
    if pos < 0:
        raise ValueError('"filenames:" not found in search index')
    filenames = searchindex[pos + len(query) + 1:]
    filenames = filenames[:filenames.find(']')]
    filenames = [f.strip('"') for f in filenames.split(',')]

    return filenames, objects


class SphinxDocLinkResolver(object):
    """ Resolve documentation links using searchindex.js generated by Sphinx

    Parameters
    ----------
    doc_url : str
        The base URL of the project website.
    searchindex : str
        Filename of searchindex, relative to doc_url.
    extra_modules_test : list of str
        List of extra module names to test.
    relative : bool
        Return relative links (only useful for links to documentation of this
        package).
    """

    def __init__(self, doc_url, searchindex='searchindex.js',
                 extra_modules_test=None, relative=False):
        self.doc_url = doc_url
        self.relative = relative
        self._link_cache = {}

        self.extra_modules_test = extra_modules_test
        self._page_cache = {}
        if doc_url.startswith('http://'):
            if relative:
                raise ValueError('Relative links are only supported for local '
                                 'URLs (doc_url cannot start with "http://)"')
            searchindex_url = doc_url + '/' + searchindex
        else:
            searchindex_url = os.path.join(doc_url, searchindex)

        # detect if we are using relative links on a Windows system
        if os.name.lower() == 'nt' and not doc_url.startswith('http://'):
            if not relative:
                raise ValueError('You have to use relative=True for the local'
                                 'package on a Windows system.')
            self._is_windows = True
        else:
            self._is_windows = False

        # download and initialize the search index
        sindex = get_data(searchindex_url)
        filenames, objects = parse_sphinx_searchindex(sindex)

        self._searchindex = dict(filenames=filenames, objects=objects)

    def _get_link(self, cobj):
        """Get a valid link, False if not found"""

        fname_idx = None
        full_name = cobj['module_short'] + '.' + cobj['name']
        if full_name in self._searchindex['objects']:
            value = self._searchindex['objects'][full_name]
            if isinstance(value, dict):
                value = value[value.keys()[0]]
            fname_idx = value[0]
        elif cobj['module_short'] in self._searchindex['objects']:
            value = self._searchindex['objects'][cobj['module_short']]
            if cobj['name'] in value.keys():
                fname_idx = value[cobj['name']][0]

        if fname_idx is not None:
            fname = self._searchindex['filenames'][fname_idx] + '.html'

            if self._is_windows:
                fname = fname.replace('/', '\\')
                link = os.path.join(self.doc_url, fname)
            else:
                link = posixpath.join(self.doc_url, fname)

            if link in self._page_cache:
                html = self._page_cache[link]
            else:
                html = get_data(link)
                self._page_cache[link] = html

            # test if cobj appears in page
            comb_names = [cobj['module_short'] + '.' + cobj['name']]
            if self.extra_modules_test is not None:
                for mod in self.extra_modules_test:
                    comb_names.append(mod + '.' + cobj['name'])
            url = False
            for comb_name in comb_names:
                if html.find(comb_name) >= 0:
                    url = link + '#' + comb_name
            link = url
        else:
            link = False

        return link

    def resolve(self, cobj, this_url):
        """Resolve the link to the documentation, returns None if not found

        Parameters
        ----------
        cobj : dict
            Dict with information about the "code object" for which we are
            resolving a link.
            cobi['name'] : function or class name (str)
            cobj['module_short'] : shortened module name (str)
            cobj['module'] : module name (str)
        this_url: str
            URL of the current page. Needed to construct relative URLs
            (only used if relative=True in constructor).

        Returns
        -------
        link : str | None
            The link (URL) to the documentation.
        """
        full_name = cobj['module_short'] + '.' + cobj['name']
        link = self._link_cache.get(full_name, None)
        if link is None:
            # we don't have it cached
            link = self._get_link(cobj)
            # cache it for the future
            self._link_cache[full_name] = link

        if link is False or link is None:
            # failed to resolve
            return None

        if self.relative:
            link = os.path.relpath(link, start=this_url)
            if self._is_windows:
                # replace '\' with '/' so it on the web
                link = link.replace('\\', '/')

            # for some reason, the relative link goes one directory too high up
            link = link[3:]

        return link


###############################################################################
rst_template = """

.. _example_%(short_fname)s:

%(docstring)s

**Python source code:** :download:`%(fname)s <%(fname)s>`

.. literalinclude:: %(fname)s
    :lines: %(end_row)s-
    """

plot_rst_template = """

.. _example_%(short_fname)s:

%(docstring)s

%(image_list)s

%(stdout)s

**Python source code:** :download:`%(fname)s <%(fname)s>`

.. literalinclude:: %(fname)s
    :lines: %(end_row)s-

**Total running time of the example:** %(time_elapsed) .2f seconds
    """

# The following strings are used when we have several pictures: we use
# an html div tag that our CSS uses to turn the lists into horizontal
# lists.
HLIST_HEADER = """
.. rst-class:: horizontal

"""

HLIST_IMAGE_TEMPLATE = """
    *

      .. image:: images/%s
            :scale: 47
"""

SINGLE_IMAGE = """
.. image:: images/%s
    :align: center
"""


def extract_docstring(filename, ignore_heading=False):
    """ Extract a module-level docstring, if any
    """
    if sys.version_info >= (3, 0):
        lines = open(filename, encoding='utf-8').readlines()
    else:
        lines = open(filename).readlines()
    start_row = 0
    if lines[0].startswith('#!'):
        lines.pop(0)
        start_row = 1
    docstring = ''
    first_par = ''
    line_iterator = iter(lines)
    tokens = tokenize.generate_tokens(lambda: next(line_iterator))
    for tok_type, tok_content, _, (erow, _), _ in tokens:
        tok_type = token.tok_name[tok_type]
        if tok_type in ('NEWLINE', 'COMMENT', 'NL', 'INDENT', 'DEDENT'):
            continue
        elif tok_type == 'STRING':
            docstring = eval(tok_content)
            # If the docstring is formatted with several paragraphs, extract
            # the first one:
            paragraphs = '\n'.join(
                line.rstrip() for line
                in docstring.split('\n')).split('\n\n')
            if paragraphs:
                if ignore_heading:
                    if len(paragraphs) > 1:
                        first_par = re.sub('\n', ' ', paragraphs[1])
                        first_par = ((first_par[:95] + '...')
                                     if len(first_par) > 95 else first_par)
                    else:
                        raise ValueError("Docstring not found by gallery",
                                         "Please check your example's layout",
                                         " and make sure it's correct")
                else:
                    first_par = paragraphs[0]

        break
    return docstring, first_par, erow + 1 + start_row


def generate_example_rst(app):
    """ Generate the list of examples, as well as the contents of
        examples.
    """
    root_dir = os.path.join(app.builder.srcdir, 'auto_examples')
    example_dir = os.path.abspath(app.builder.srcdir + '/../../' + 'examples')
    try:
        plot_gallery = eval(app.builder.config.plot_gallery)
    except TypeError:
        plot_gallery = bool(app.builder.config.plot_gallery)
    if not os.path.exists(example_dir):
        os.makedirs(example_dir)
    if not os.path.exists(root_dir):
        os.makedirs(root_dir)

    # we create an index.rst with all examples
    fhindex = open(os.path.join(root_dir, 'index.rst'), 'w')
    fhindex.write("""\



.. raw:: html


    <style type="text/css">

    .figure {
        float: left;
        margin: 10px;
        -webkit-border-radius: 10px; /* Saf3-4, iOS 1-3.2, Android <1.6 */
        -moz-border-radius: 10px; /* FF1-3.6 */
        border-radius: 10px; /* Opera 10.5, IE9, Saf5, Chrome, FF4, iOS 4, Android 2.1+ */
        border: 2px solid #fff;
        background-color: white;
        /* --> Thumbnail image size */
        width: 150px;
        height: 100px;
        -webkit-background-size: 150px 100px; /* Saf3-4 */
        -moz-background-size: 150px 100px; /* FF3.6 */
    }

    .figure img {
        display: inline;
    }

    div.docstringWrapper p.caption {
        display: block;
        -webkit-box-shadow: 0px 0px 20px rgba(0, 0, 0, 0.0);
        -moz-box-shadow: 0px 0px 20px rgba(0, 0, 0, .0); /* FF3.5 - 3.6 */
        box-shadow: 0px 0px 20px rgba(0, 0, 0, 0.0); /* Opera 10.5, IE9, FF4+, Chrome 10+ */
        padding: 0px;
        border: white;
    }

    div.docstringWrapper p {
        display: none;
        background-color: white;
        -webkit-box-shadow: 0px 0px 20px rgba(0, 0, 0, 1.00);
        -moz-box-shadow: 0px 0px 20px rgba(0, 0, 0, 1.00); /* FF3.5 - 3.6 */
        box-shadow: 0px 0px 20px rgba(0, 0, 0, 1.00); /* Opera 10.5, IE9, FF4+, Chrome 10+ */
        padding: 13px;
        margin-top: 0px;
        border-style: solid;
        border-width: 1px;
    }


    </style>


.. raw:: html


        <script type="text/javascript">

        function animateClone(e){
          var position;
          position = $(this).position();
          var clone = $(this).closest('.thumbnailContainer').find('.clonedItem');
          var clone_fig = clone.find('.figure');
          clone.css("left", position.left - 70).css("top", position.top - 70).css("position", "absolute").css("z-index", 1000).css("background-color", "white");

          var cloneImg = clone_fig.find('img');

          clone.show();
          clone.animate({
                height: "270px",
                width: "320px"
            }, 0
          );
          cloneImg.css({
                'max-height': "200px",
                'max-width': "280px"
          });
          cloneImg.animate({
                height: "200px",
                width: "280px"
            }, 0
           );
          clone_fig.css({
               'margin-top': '20px',
          });
          clone_fig.show();
          clone.find('p').css("display", "block");
          clone_fig.css({
               height: "240",
               width: "305px"
          });
          cloneP_height = clone.find('p.caption').height();
          clone_fig.animate({
               height: (200 + cloneP_height)
           }, 0
          );

          clone.bind("mouseleave", function(e){
              clone.animate({
                  height: "100px",
                  width: "150px"
              }, 10, function(){$(this).hide();});
              clone_fig.animate({
                  height: "100px",
                  width: "150px"
              }, 10, function(){$(this).hide();});
          });
        } //end animateClone()


        $(window).load(function () {
            $(".figure").css("z-index", 1);

            $(".docstringWrapper").each(function(i, obj){
                var clone;
                var $obj = $(obj);
                clone = $obj.clone();
                clone.addClass("clonedItem");
                clone.appendTo($obj.closest(".thumbnailContainer"));
                clone.hide();
                $obj.bind("mouseenter", animateClone);
            }); // end each
        }); // end

        </script>



Examples
========

.. _examples-index:
""")
    # Here we don't use an os.walk, but we recurse only twice: flat is
    # better than nested.
    generate_dir_rst('.', fhindex, example_dir, root_dir, plot_gallery)
    for dir in sorted(os.listdir(example_dir)):
        if os.path.isdir(os.path.join(example_dir, dir)):
            generate_dir_rst(dir, fhindex, example_dir, root_dir, plot_gallery)
    fhindex.flush()


def extract_line_count(filename, target_dir):
    # Extract the line count of a file
    example_file = os.path.join(target_dir, filename)
    if sys.version_info >= (3, 0):
        lines = open(example_file, encoding='utf-8').readlines()
    else:
        lines = open(example_file).readlines()
    start_row = 0
    if lines and lines[0].startswith('#!'):
        lines.pop(0)
        start_row = 1
    line_iterator = iter(lines)
    tokens = tokenize.generate_tokens(lambda: next(line_iterator))
    check_docstring = True
    erow_docstring = 0
    for tok_type, _, _, (erow, _), _ in tokens:
        tok_type = token.tok_name[tok_type]
        if tok_type in ('NEWLINE', 'COMMENT', 'NL', 'INDENT', 'DEDENT'):
            continue
        elif ((tok_type == 'STRING') and check_docstring):
            erow_docstring = erow
            check_docstring = False
    return erow_docstring+1+start_row, erow+1+start_row


def line_count_sort(file_list, target_dir):
    # Sort the list of examples by line-count
    new_list = [x for x in file_list if x.endswith('.py')]
    unsorted = np.zeros(shape=(len(new_list), 2))
    unsorted = unsorted.astype(np.object)
    for count, exmpl in enumerate(new_list):
        docstr_lines, total_lines = extract_line_count(exmpl, target_dir)
        unsorted[count][1] = total_lines - docstr_lines
        unsorted[count][0] = exmpl
    index = np.lexsort((unsorted[:, 0].astype(np.str),
                        unsorted[:, 1].astype(np.float64)))
    if not len(unsorted):
        return []
    return np.array(unsorted[index][:, 0]).tolist()


def generate_dir_rst(dir, fhindex, example_dir, root_dir, plot_gallery):
    """ Generate the rst file for an example directory.
    """
    if not dir == '.':
        target_dir = os.path.join(root_dir, dir)
        src_dir = os.path.join(example_dir, dir)
    else:
        target_dir = root_dir
        src_dir = example_dir
    if not os.path.exists(os.path.join(src_dir, 'README.txt')):
        print(80 * '_')
        print('Example directory %s does not have a README.txt file' %
              src_dir)
        print('Skipping this directory')
        print(80 * '_')
        return
    fhindex.write("""


%s


""" % open(os.path.join(src_dir, 'README.txt')).read())
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
    sorted_listdir = line_count_sort(os.listdir(src_dir),
                                     src_dir)
    for fname in sorted_listdir:
        if fname.endswith('py'):
            generate_file_rst(fname, target_dir, src_dir, plot_gallery)
            new_fname = os.path.join(src_dir, fname)
            _, fdocstring, _ = extract_docstring(new_fname, True)
            thumb = os.path.join(dir, 'images', 'thumb', fname[:-3] + '.png')
            link_name = os.path.join(dir, fname).replace(os.path.sep, '_')
            fhindex.write("""

.. raw:: html


    <div class="thumbnailContainer">
        <div class="docstringWrapper">


""")

            fhindex.write('.. figure:: %s\n' % thumb)
            if link_name.startswith('._'):
                link_name = link_name[2:]
            if dir != '.':
                fhindex.write('   :target: ./%s/%s.html\n\n' % (dir,
                                                                fname[:-3]))
            else:
                fhindex.write('   :target: ./%s.html\n\n' % link_name[:-3])
            fhindex.write("""   :ref:`example_%s`


.. raw:: html


    <p>%s
    </p></div>
    </div>


.. toctree::
   :hidden:

   %s/%s

""" % (link_name, fdocstring, dir, fname[:-3]))
    fhindex.write("""
.. raw:: html

    <div style="clear: both"></div>
    """)  # clear at the end of the section

# modules for which we embed links into example code
DOCMODULES = ['pyart', 'matplotlib', 'numpy', 'scipy']


def make_thumbnail(in_fname, out_fname, width, height):
    """Make a thumbnail with the same aspect ratio centered in an
       image with a given width and height
    """
    img = Image.open(in_fname)
    width_in, height_in = img.size
    scale_w = width / float(width_in)
    scale_h = height / float(height_in)

    if height_in * scale_w <= height:
        scale = scale_w
    else:
        scale = scale_h

    width_sc = int(round(scale * width_in))
    height_sc = int(round(scale * height_in))

    # resize the image
    img.thumbnail((width_sc, height_sc), Image.ANTIALIAS)

    # insert centered
    thumb = Image.new('RGB', (width, height), (255, 255, 255))
    pos_insert = ((width - width_sc) // 2, (height - height_sc) // 2)
    thumb.paste(img, pos_insert)

    thumb.save(out_fname)


def get_short_module_name(module_name, obj_name):
    """ Get the shortest possible module name """
    parts = module_name.split('.')
    short_name = module_name
    for i in range(len(parts) - 1, 0, -1):
        short_name = '.'.join(parts[:i])
        try:
            exec('from %s import %s' % (short_name, obj_name))
        except ImportError:
            # get the last working module name
            short_name = '.'.join(parts[:(i + 1)])
            break
    return short_name


def generate_file_rst(fname, target_dir, src_dir, plot_gallery):
    """ Generate the rst file for a given example.
    """
    base_image_name = os.path.splitext(fname)[0]
    image_fname = '%s_%%s.png' % base_image_name

    this_template = rst_template
    last_dir = os.path.split(src_dir)[-1]
    # to avoid leading . in file names, and wrong names in links
    if last_dir == '.' or last_dir == 'examples':
        last_dir = ''
    else:
        last_dir += '_'
    short_fname = last_dir + fname
    src_file = os.path.join(src_dir, fname)
    example_file = os.path.join(target_dir, fname)
    shutil.copyfile(src_file, example_file)

    # The following is a list containing all the figure names
    figure_list = []

    image_dir = os.path.join(target_dir, 'images')
    thumb_dir = os.path.join(image_dir, 'thumb')
    if not os.path.exists(image_dir):
        os.makedirs(image_dir)
    if not os.path.exists(thumb_dir):
        os.makedirs(thumb_dir)
    image_path = os.path.join(image_dir, image_fname)
    stdout_path = os.path.join(image_dir,
                               'stdout_%s.txt' % base_image_name)
    time_path = os.path.join(image_dir,
                             'time_%s.txt' % base_image_name)
    thumb_file = os.path.join(thumb_dir, fname[:-3] + '.png')
    time_elapsed = 0
    if plot_gallery and fname.startswith('plot'):
        # generate the plot as png image if file name
        # starts with plot and if it is more recent than an
        # existing image.
        first_image_file = image_path % 1
        if os.path.exists(stdout_path):
            stdout = open(stdout_path).read()
        else:
            stdout = ''
        if os.path.exists(time_path):
            time_elapsed = float(open(time_path).read())

        if not os.path.exists(first_image_file) or \
           os.stat(first_image_file).st_mtime <= os.stat(src_file).st_mtime:
            # We need to execute the code
            print('plotting %s' % fname)
            t0 = time()
            import matplotlib.pyplot as plt
            plt.close('all')
            cwd = os.getcwd()
            try:
                # First CD in the original example dir, so that any file
                # created by the example get created in this directory
                orig_stdout = sys.stdout
                os.chdir(os.path.dirname(src_file))
                my_buffer = StringIO()
                my_stdout = Tee(sys.stdout, my_buffer)
                sys.stdout = my_stdout
                my_globals = {'pl': plt}
                execfile(os.path.basename(src_file), my_globals)
                time_elapsed = time() - t0
                sys.stdout = orig_stdout
                my_stdout = my_buffer.getvalue()

                # get variables so we can later add links to the documentation
                example_code_obj = {}
                for var_name, var in my_globals.items():
                    if not hasattr(var, '__module__'):
                        continue
                    if not isinstance(var.__module__, basestring):
                        continue
                    if var.__module__.split('.')[0] not in DOCMODULES:
                        continue

                    # get the type as a string with other things stripped
                    tstr = str(type(var))
                    tstr = (tstr[tstr.find('\'')
                            + 1:tstr.rfind('\'')].split('.')[-1])
                    # get shortened module name
                    module_short = get_short_module_name(var.__module__,
                                                         tstr)
                    cobj = {'name': tstr, 'module': var.__module__,
                            'module_short': module_short,
                            'obj_type': 'object'}
                    example_code_obj[var_name] = cobj

                # find functions so we can later add links to the documentation
                funregex = re.compile('[\w.]+\(')
                with open(src_file, 'rt') as fid:
                    for line in fid.readlines():
                        if line.startswith('#'):
                            continue
                        for match in funregex.findall(line):
                            fun_name = match[:-1]

                            try:
                                exec('this_fun = %s' % fun_name, my_globals)
                            except Exception as err:
                                # Here, we were not able to execute the
                                # previous statement, either because the
                                # fun_name was not a function but a statement
                                # (print), or because the regexp didn't
                                # catch the whole function name :
                                #    eg:
                                #       X = something().blah()
                                # will work for something, but not blah.

                                continue
                            this_fun = my_globals['this_fun']
                            if not callable(this_fun):
                                continue
                            if not hasattr(this_fun, '__module__'):
                                continue
                            if not isinstance(this_fun.__module__, basestring):
                                continue
                            if (this_fun.__module__.split('.')[0]
                                    not in DOCMODULES):
                                continue

                            # get shortened module name
                            fun_name_short = fun_name.split('.')[-1]
                            module_short = get_short_module_name(
                                this_fun.__module__, fun_name_short)
                            cobj = {'name': fun_name_short,
                                    'module': this_fun.__module__,
                                    'module_short': module_short,
                                    'obj_type': 'function'}
                            example_code_obj[fun_name] = cobj
                fid.close()

                if len(example_code_obj) > 0:
                    # save the dictionary, so we can later add hyperlinks
                    codeobj_fname = example_file[:-3] + '_codeobj.pickle'
                    with open(codeobj_fname, 'wb') as fid:
                        pickle.dump(example_code_obj, fid,
                                    pickle.HIGHEST_PROTOCOL)
                    fid.close()

                if '__doc__' in my_globals:
                    # The __doc__ is often printed in the example, we
                    # don't with to echo it
                    my_stdout = my_stdout.replace(
                        my_globals['__doc__'],
                        '')
                my_stdout = my_stdout.strip()
                if my_stdout:
                    stdout = '**Script output**::\n\n  %s\n\n' % (
                        '\n  '.join(my_stdout.split('\n')))
                open(stdout_path, 'w').write(stdout)
                open(time_path, 'w').write('%f' % time_elapsed)
                os.chdir(cwd)

                # In order to save every figure we have two solutions :
                # * iterate from 1 to infinity and call plt.fignum_exists(n)
                #   (this requires the figures to be numbered
                #    incrementally: 1, 2, 3 and not 1, 2, 5)
                # * iterate over [fig_mngr.num for fig_mngr in
                #   matplotlib._pylab_helpers.Gcf.get_all_fig_managers()]
                for fig_num in (fig_mngr.num for fig_mngr in
                    matplotlib._pylab_helpers.Gcf.get_all_fig_managers()):
                    # Set the fig_num figure as the current figure as we can't
                    # save a figure that's not the current figure.
                    plt.figure(fig_num)
                    plt.savefig(image_path % fig_num)
                    figure_list.append(image_fname % fig_num)
            except:
                print(80 * '_')
                print('%s is not compiling:' % fname)
                traceback.print_exc()
                print(80 * '_')
            finally:
                os.chdir(cwd)
                sys.stdout = orig_stdout

            print(" - time elapsed : %.2g sec" % time_elapsed)
        else:
            figure_list = [f[len(image_dir):]
                            for f in glob.glob(image_path % '[1-9]')]
                            #for f in glob.glob(image_path % '*')]

        # generate thumb file
        this_template = plot_rst_template
        if os.path.exists(first_image_file):
            make_thumbnail(first_image_file, thumb_file, 400, 280)

    if not os.path.exists(thumb_file):
        # create something to replace the thumbnail
        make_thumbnail('images/no_image.png', thumb_file, 200, 140)

    docstring, short_desc, end_row = extract_docstring(example_file)

    # Depending on whether we have one or more figures, we're using a
    # horizontal list or a single rst call to 'image'.
    if len(figure_list) == 1:
        figure_name = figure_list[0]
        image_list = SINGLE_IMAGE % figure_name.lstrip('/')
    else:
        image_list = HLIST_HEADER
        for figure_name in figure_list:
            image_list += HLIST_IMAGE_TEMPLATE % figure_name.lstrip('/')

    f = open(os.path.join(target_dir, fname[:-2] + 'rst'), 'w')
    f.write(this_template % locals())
    f.flush()


def embed_code_links(app, exception):
    """Embed hyperlinks to documentation into example code"""
    try:
        if exception is not None:
            return
        print('Embedding documentation hyperlinks in examples..')

        # Add resolvers for the packages for which we want to show links
        doc_resolvers = {}
        doc_resolvers['pyart'] = SphinxDocLinkResolver(app.builder.outdir,
                                                       relative=True)

        doc_resolvers['matplotlib'] = SphinxDocLinkResolver(
            'http://matplotlib.org')

        doc_resolvers['numpy'] = SphinxDocLinkResolver(
            'http://docs.scipy.org/doc/numpy-1.6.0')

        doc_resolvers['scipy'] = SphinxDocLinkResolver(
            'http://docs.scipy.org/doc/scipy-0.11.0/reference')

        example_dir = os.path.join(app.builder.srcdir, 'auto_examples')
        html_example_dir = os.path.abspath(os.path.join(app.builder.outdir,
                                                        'auto_examples'))

        # patterns for replacement
        link_pattern = '<a href="%s">%s</a>'
        orig_pattern = '<span class="n">%s</span>'
        period = '<span class="o">.</span>'

        for dirpath, _, filenames in os.walk(html_example_dir):
            for fname in filenames:
                print('\tprocessing: %s' % fname)
                full_fname = os.path.join(html_example_dir, dirpath, fname)
                subpath = dirpath[len(html_example_dir) + 1:]
                pickle_fname = os.path.join(example_dir, subpath,
                                            fname[:-5] + '_codeobj.pickle')

                if os.path.exists(pickle_fname):
                    # we have a pickle file with the objects to embed links for
                    with open(pickle_fname, 'rb') as fid:
                        example_code_obj = pickle.load(fid)
                    fid.close()
                    str_repl = {}
                    # generate replacement strings with the links
                    for name, cobj in example_code_obj.iteritems():
                        this_module = cobj['module'].split('.')[0]

                        if this_module not in doc_resolvers:
                            continue

                        link = doc_resolvers[this_module].resolve(cobj,
                                                                  full_fname)
                        if link is not None:
                            parts = name.split('.')
                            name_html = orig_pattern % parts[0]
                            for part in parts[1:]:
                                name_html += period + orig_pattern % part
                            str_repl[name_html] = link_pattern % (link, name_html)
                    # do the replacement in the html file
                    if len(str_repl) > 0:
                        with open(full_fname, 'rb') as fid:
                            lines_in = fid.readlines()
                        with open(full_fname, 'wb') as fid:
                            for line in lines_in:
                                line = line.decode('utf-8')
                                for name, link in str_repl.iteritems():
                                    line = line.replace(name, link)
                                fid.write(line.encode('utf-8'))
    except HTTPError as e:
        print("The following HTTP Error has occurred:\n")
        print(e.code)
    except URLError as e:
        print("\n...\n"
              "Warning: Embedding the documentation hyperlinks requires "
              "internet access.\nPlease check your network connection.\n"
              "Unable to continue embedding due to a URL Error: \n")
        print(e.args)
    print('[done]')


def setup(app):
    app.connect('builder-inited', generate_example_rst)
    app.add_config_value('plot_gallery', True, 'html')

    # embed links after build is finished
    # XXX disable documentation hyperlinks in examples for now
    #app.connect('build-finished', embed_code_links)

    # Sphinx hack: sphinx copies generated images to the build directory
    #  each time the docs are made.  If the desired image name already
    #  exists, it appends a digit to prevent overwrites.  The problem is,
    #  the directory is never cleared.  This means that each time you build
    #  the docs, the number of images in the directory grows.
    #
    # This question has been asked on the sphinx development list, but there
    #  was no response: http://osdir.com/ml/sphinx-dev/2011-02/msg00123.html
    #
    # The following is a hack that prevents this behavior by clearing the
    #  image build directory each time the docs are built.  If sphinx
    #  changes their layout between versions, this will not work (though
    #  it should probably not cause a crash).  Tested successfully
    #  on Sphinx 1.0.7
    build_image_dir = 'build/html/_images'
    if os.path.exists(build_image_dir):
        filelist = os.listdir(build_image_dir)
        for filename in filelist:
            if filename.endswith('png'):
                os.remove(os.path.join(build_image_dir, filename))
