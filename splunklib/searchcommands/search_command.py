# Copyright 2011-2015 Splunk, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"): you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from __future__ import absolute_import, division, print_function, unicode_literals

# Absolute imports

from splunklib.client import Service

from collections import OrderedDict, namedtuple
from inspect import getmembers
from itertools import chain, imap, izip
from logging import _levelNames, getLevelName
from numbers import Number
from os import environ
from sys import argv, exit, stdin, stdout
from urlparse import urlsplit
from xml.etree import ElementTree

import sys
import re
import json

# Relative imports

from . import logging
from .decorators import Option
from .validators import Boolean

SearchMetric = namedtuple(b'Metric', (b'elapsed_seconds', b'invocation_count', b'input_count', b'output_count'))


class SearchCommand(object):
    """ Represents a custom search command.

    """

    def __init__(self, app_root=None):
        """
        :param app_root: The root of the application directory, used primarily by tests.
        :type app_root: str or NoneType
        """

        # Variables that may be used, but not altered by derived classes

        self.logger, self._logging_configuration = logging.configure(type(self).__name__, app_root=app_root)

        if 'SPLUNK_HOME' not in environ:
            self.logger.warning(
                'SPLUNK_HOME environment variable is undefined.\n'
                'If you are testing outside of Splunk, consider running under control of the Splunk CLI:\n'
                '    splunk cmd %s\n'
                'If you are running inside of Splunk, SPLUNK_HOME should be defined. Consider troubleshooting your '
                'installation.', self)

        # Variables backing option/property values

        self._app_root = app_root
        self._configuration = None
        self._fieldnames = None
        self._inspector = OrderedDict()
        self._metadata = None
        self._option_view = None
        self._output_file = None
        self._search_results_info = None
        self._service = None

        # Internal variables

        self._default_logging_level = self.logger.level
        self._message_count = None

    def __repr__(self):
        return str(self)

    def __str__(self):
        values = [type(self).name, str(self.options)] + self.fieldnames
        text = ' '.join([value for value in values if len(value) > 0])
        return text

    # region Options

    @Option
    def logging_configuration(self):
        """ **Syntax:** logging_configuration=<path>

        **Description:** Loads an alternative logging configuration file for
        a command invocation. The logging configuration file must be in Python
        ConfigParser-format. Path names are relative to the app root directory.

        """
        return self._logging_configuration

    @logging_configuration.setter
    def logging_configuration(self, value):
        self.logger, self._logging_configuration = logging.configure(
            type(self).__name__, value, app_root=self._app_root)
        return

    @Option
    def logging_level(self):
        """ **Syntax:** logging_level=[CRITICAL|ERROR|WARNING|INFO|DEBUG|NOTSET]

        **Description:** Sets the threshold for the logger of this command
        invocation. Logging messages less severe than `logging_level` will be
        ignored.

        """
        return getLevelName(self.logger.getEffectiveLevel())

    @logging_level.setter
    def logging_level(self, value):
        if value is None:
            value = self._default_logging_level
        if type(value) is str:
            try:
                level = _levelNames[value.upper()]
            except KeyError:
                raise ValueError('Unrecognized logging level: %s' % value)
        else:
            try:
                level = int(value)
            except ValueError:
                raise ValueError('Unrecognized logging level: %s' % value)
        self.logger.setLevel(level)
        return

    show_configuration = Option(doc='''
        **Syntax:** show_configuration=<bool>

        **Description:** When `true`, reports command configuration in the
        messages header for this command invocation. Defaults to `false`.

        ''', default=False, validate=Boolean())

    # endregion

    # region Properties

    @property
    def configuration(self):
        """ Returns the configuration settings for this command.

        """
        return self._configuration

    @property
    def fieldnames(self):
        """ Returns the fieldnames specified as argument to this command.

        """
        return self._fieldnames

    @fieldnames.setter
    def fieldnames(self, value):
        self._fieldnames = value

    @property
    def metadata(self):
        return self._metadata

    @property
    def options(self):
        """ Returns the options specified as argument to this command.

        """
        if self._option_view is None:
            self._option_view = Option.View(self)
        return self._option_view

    @property
    def search_results_info(self):
        """ Returns the search results info for this command invocation or None.

        The search results info object is created from the search results info
        file associated with the command invocation. Splunk does not pass the
        location of this file by default. You must request it by specifying
        these configuration settings in commands.conf:

        .. code-block:: python
            enableheader=true
            requires_srinfo=true

        The :code:`enableheader` setting is :code:`true` by default. Hence, you
        need not set it. The :code:`requires_srinfo` setting is false by
        default. Hence, you must set it.

        :return: :class:`SearchResultsInfo`, if :code:`enableheader` and
            :code:`requires_srinfo` are both :code:`true`. Otherwise, if either
            :code:`enableheader` or :code:`requires_srinfo` are :code:`false`,
            a value of :code:`None` is returned.

        """
        if self._search_results_info is not None:
            return self._search_results_info

        try:
            info_path = self.metadata['infoPath']
        except KeyError:
            return None

        def convert_field(field):
            return (field[1:] if field[0] == '_' else field).replace('.', '_')

        def convert_value(field, value):

            if field == 'countMap':
                split = value.split(';')
                value = dict((key, int(value)) for key, value in zip(split[0::2], split[1::2]))
            elif field == 'vix_families':
                value = ElementTree.fromstring(value)
            elif value == '':
                value = None
            else:
                try:
                    value = float(value)
                    if value.is_integer():
                        value = int(value)
                except ValueError:
                    pass

            return value

        with open(info_path, 'rb') as f:
            from collections import namedtuple
            import csv
            reader = csv.reader(f, dialect='splunklib.searchcommands')
            fields = [convert_field(x) for x in reader.next()]
            values = [convert_value(f, v) for f, v in zip(fields, reader.next())]

        search_results_info_type = namedtuple('SearchResultsInfo', fields)
        self._search_results_info = search_results_info_type._make(values)

        return self._search_results_info

    @property
    def service(self):
        """ Returns a Splunk service object for this command invocation or None.

        The service object is created from the Splunkd URI and authentication
        token passed to the command invocation in the search results info file.
        This data is not passed to a command invocation by default. You must
        request it by specifying this pair of configuration settings in
        commands.conf:

           .. code-block:: python
               enableheader=true
               requires_srinfo=true

        The :code:`enableheader` setting is :code:`true` by default. Hence, you
        need not set it. The :code:`requires_srinfo` setting is false by
        default. Hence, you must set it.

        :return: :class:`splunklib.client.Service`, if :code:`enableheader` and
            :code:`requires_srinfo` are both :code:`true`. Otherwise, if either
            :code:`enableheader` or :code:`requires_srinfo` are :code:`false`,
            a value of :code:`None` is returned.

        """
        if self._service is not None:
            return self._service

        info = self.search_results_info

        if info is None:
            return None

        splunkd = urlsplit(info.splunkd_uri, info.splunkd_protocol, allow_fragments=False)

        self._service = Service(
            scheme=splunkd.scheme, host=splunkd.hostname, port=splunkd.port, token=info.auth_token, app=info.ppc_app)

        return self._service

    # endregion

    # region Methods

    def error_exit(self, error, message=None):
        self.write_error(error.message.capitalize() if message is None else message)
        self.logger.error('Abnormal exit: %s', error)
        exit(1)

    def process(self, args=argv, ifile=stdin, ofile=stdout):
        """ Processes records on the `input stream optionally writing records to the output stream.

        :param args: Unused.

        :param ifile: Input file object.
        :type ifile: file

        :param ofile: Output file object.
        :type ofile: file

        :return: :const:`None`

        """
        try:
            # getInfo exchange

            # TODO: Expose metadata object to SearchCommand and utilize it in SearchCommand.search_results_info

            metadata, body = self._read_chunk(ifile)
            self.fieldnames = []
            self.options.reset()
            self._message_count = 0L

            if 'args' in metadata and type(metadata['args']) == list:
                for arg in metadata['args']:
                    result = arg.split('=', 1)
                    if len(result) == 1:
                        self.fieldnames.append(result[0])
                    else:
                        name, value = result
                        try:
                            option = self.options[name]
                        except KeyError:
                            self.write_error('Unrecognized option: {0}'.format(result))
                            continue
                        try:
                            option.value = value
                        except ValueError:
                            self.write_error('Illegal value: {0}'.format(option))
                            continue
                    pass
                pass

            missing = self.options.get_missing()

            if missing is not None:
                if len(missing) == 1:
                    self.write_error('A value for "{0}" is required'.format(missing[0]))
                else:
                    self.write_error('Values for these options are required: {0}'.format(', '.join(missing)))

            if self._message_count > 0:
                exit(1)

            self._configuration = type(self).ConfigurationSettings(self)
            self._metadata = metadata

            self.prepare()

            if self.show_configuration:
                self.write_info('{0} command configuration settings: {1}'.format(self.name, self.configuration))

            metadata = OrderedDict(chain(self.configuration.iteritems(), (('inspector', self._inspector),)))
            self._write_chunk(ofile, metadata, '')
            self._inspector.clear()
            ofile.write('\n')

            self._execute(ifile, ofile)
            pass

        except SystemExit:
            # TODO: Fail gracefully as per protocol
            raise

        except:

            import traceback
            import sys

            error_type, error_message, error_traceback = sys.exc_info()
            self.logger.error(traceback.format_exc(error_traceback))

            origin = error_traceback

            while origin.tb_next is not None:
                origin = origin.tb_next

            filename = origin.tb_frame.f_code.co_filename
            lineno = origin.tb_lineno

            self.write_error('%s at "%s", line %d : %s', error_type.__name__, filename, lineno, error_message)

            # TODO: Fail gracefully as per protocol
            exit(1)

        return

    def prepare(self):
        """ Prepare for execution.

        This method should be overridden in search command classes that wish to examine, and update their configuration
        settings prior to execution. It is called during the getInfo exchange before command metadata is sent to
        splunkd.

        :return: :const:`None`

        """
        pass

    def write_debug(self, message, *args):
        self._write_message('DEBUG', message, *args)
        return

    def write_error(self, message, *args):
        self._write_message('ERROR', message, *args)
        return

    def write_fatal(self, message, *args):
        self._write_message('FATAL', message, *args)
        return

    def write_info(self, message, *args):
        self._write_message('INFO', message, *args)
        return

    def write_warning(self, message, *args):
        self._write_message('WARN', message, *args)

    def write_metric(self, name, value):
        """ Writes a metric that will be added to the search inspector.

        :param name: Name of the metric.
        :type name: basestring

        :param value: A 4-tuple containing the value of metric :param:`name` where

            value[0] = Elapsed seconds or :const:`None`.
            value[1] = Number of invocations or :const:`None`.
            value[2] = Input count or :const:`None`.
            value[3] = Output count or :const:`None`.

        The :data:`SearchMetric` type provides a convenient encapsulation of :param:`value`.
        The :data:`SearchMetric` type provides a convenient encapsulation of :param:`value`.

        :return: :const:`None`.

        """
        self._inspector['metric.{0}'.format(name)] = value
        return

    # TODO: Support custom inspector values

    @staticmethod
    def _decode_list(mv):
        if len(mv) == 0:
            return None
        in_value = False
        value = ''
        i = 0
        l = []
        while i < len(mv):
            if not in_value:
                if mv[i] == '$':
                    in_value = True
                elif mv[i] != ';':
                    return None
            else:
                if mv[i] == '$' and i + 1 < len(mv) and mv[i + 1] == '$':
                    value += '$'
                    i += 1
                elif mv[i] == '$':
                    in_value = False
                    l.append(value)
                    value = ''
                else:
                    value += mv[i]
            i += 1
        return l

    @staticmethod
    def _encode_list(value):

        def to_string(item):
            if isinstance(item, bool):
                return 't' if item else 'f'
            if isinstance(item, (basestring, Number)):
                return unicode(item)
            return repr(item)

        if len(value) == 0:
            return None, None

        if len(value) == 1:
            return to_string(value[0]), None

        # TODO: Bug fix: If a list item contains newlines, value cannot be interpreted correctly

        value = imap(lambda item: (item, item.replace('$', '$$')), imap(lambda item: to_string(item), value))

        return '\n'.join(imap(lambda item: item[0], value)), '$' + '$;$'.join(imap(lambda item: item[1], value)) + '$'

    def _execute(self, ifile, ofile):
        """ Execution loop.

        :param ifile: Input file object.
        :type ifile: file

        :param ofile: Output file object.
        :type ofile: file

        :return: `None`.

        """
        raise NotImplementedError('SearchCommand._execute(self)')

    def _records(self, reader):
        record_count = 0L

        try:
            fieldnames = reader.next()
        except StopIteration:
            return

        mv_fieldnames = set()

        for fieldname in fieldnames:
            if fieldname.startswith('__mv_'):
                mv_fieldnames.add(fieldname[len('__mv_')])
            pass

        if len(mv_fieldnames):
            for values in reader:
                record = OrderedDict()
                for fieldname, value in izip(fieldnames, record):
                    if fieldname.startswith('__mv_'):
                        values[fieldname[len('__mv_'):]] = self._decode_list(value)
                    elif fieldname not in mv_fieldnames:
                        values[fieldname] = value
                record_count += 1L
                yield record
        else:
            for values in reader:
                record = OrderedDict(izip(fieldnames, values))
                record_count += 1
                yield record

        return

    @staticmethod
    def _read_chunk(f):
        try:
            header = f.readline()
        except:
            return None

        if not header or len(header) == 0:
            return None

        m = re.match('chunked\s+1.0\s*,\s*(?P<metadata_length>\d+)\s*,\s*(?P<body_length>\d+)\s*\n', header)
        if m is None:
            print('Failed to parse transport header: {0}'.format(header), file=sys.stderr)
            return None

        try:
            metadata_length = int(m.group('metadata_length'))
            body_length = int(m.group('body_length'))
        except:
            print('Failed to parse metadata or body length', file=sys.stderr)
            return None

        print('READING CHUNK {0} {1}'.format(metadata_length, body_length), file=sys.stderr)

        try:
            metadata_buffer = f.read(metadata_length)
            body = f.read(body_length)
        except Exception as error:
            print('Failed to read metadata or body: {0}'.format(error), file=sys.stderr)
            return None

        try:
            metadata = json.loads(metadata_buffer)
        except:
            print('Failed to parse metadata JSON', file=sys.stderr)
            return None

        return [metadata, body]

    @staticmethod
    def _write_chunk(f, metadata, body):
        metadata_buffer = None
        if metadata:
            metadata_buffer = json.dumps(metadata)
        f.write('chunked 1.0,%d,%d\n' % (len(metadata_buffer) if metadata_buffer else 0, len(body)))
        f.write(metadata_buffer)
        f.write(body)
        f.flush()

    def _write_message(self, message_type, message_text, *args):
        message_text = message_text.format(args)
        self._inspector['message.{0}.{1}'.format(self._message_count, message_type)] = message_text

    # endregion

    # region Types

    class ConfigurationSettings(object):
        """ Represents the configuration settings common to all :class:`SearchCommand` classes.

        """
        def __init__(self, command):
            self.command = command
            self._finished = False

        def __str__(self):
            """ Converts the value of this instance to its string representation.

            The value of this ConfigurationSettings instance is represented as a
            string of newline-separated :code:`name=value` pairs.

            :return: String representation of this instance

            """
            text = ', '.join(imap(lambda key: '{0}={1}'.format(key, repr(getattr(self, key))), type(self)._keys))
            return text

        # region Properties

        # Constant configuration settings

        @property
        def finished(self):
            """ Signals that the search command is complete.

            Default: :const:`True`

            """
            return self._finished

        @finished.setter
        def finished(self, value):
            if not type(value) is bool:
                raise ValueError('Illegal value for partial: {0}.'.format(repr(value)))
            self._finished = value

        @property
        def partial(self):
            """ Specifies whether the search command returns its response in multiple chunks.

            There are two use-cases for this:

            1. The result is very big and we'd like to emit partial chunks that fit in memory.
            2. The field set changes dramatically and we don't want to emit a sparse record set.

            If partial is :const:`True`, this chunk is part of a multi-part response. If partial is :const:`False`, this
            chunk completes a multi-part response.

            An alternative to this metadata field is to include a chunk identifier in each record. The complication of
            using a chunk identifier is that splunkd won't know that a chunk is complete until it sees a chunk with a
            new chunk identifier. The upside to having the partial field is that the common case of 1-to-1 chunks
            requires nothing from the protocol.

            Default: :const:`False`

            """
            return getattr(self, '_partial', type(self)._partial)

        @partial.setter
        def partial(self, value):
            if not type(value) is bool:
                raise ValueError('Illegal value for partial: {0}.'.format(repr(value)))
            setattr(self, '_stderr_dest', value)

        _partial = False

        @property
        def stderr_dest(self):
            """ Tells Splunk what to do with messages logged to `stderr`.

            Specify one of these string values:

            ================== ========================================================
            Value              Meaning
            ================== ========================================================
            :code:`'log'`      Write messages to the job's search.log file.
            :code:`'none'`     Discard all messages logged to stderr.
            ================== ========================================================

            Default: :code:`'log'`

            """
            return getattr(self, '_stderr_dest', type(self)._stderr_dest)

        @stderr_dest.setter
        def stderr_dest(self, value):
            if value not in ('log', 'none'):
                raise ValueError('Illegal value for stderr_dest: {0}'.format(repr(value)))
            setattr(self, '_stderr_dest', value)

        _stderr_dest = 'log'

        # endregion

        # region Methods

        def iteritems(self):
            """ Represents this instance as an iterable over the ordered set of configuration items in this object.

            This method is used by :method:`SearchCommand.process` to report configuration settings to splunkd during
            the :code:`getInfo` exchange of the request to process search results.

            :return: :class:`OrderedDict` containing setting values keyed by name.

            """
            return imap(lambda key: (key, getattr(self, key)), type(self)._keys)

        @classmethod
        def configuration_settings(cls):
            """ Represents this class as a dictionary of :class:`property` instances and :code:`backing_field` names
            keyed by configuration setting name.

            This method is used by the :class:`ConfigurationSettingsType` meta-class to construct new
            :class:`ConfigurationSettings` classes.

            """
            if not hasattr(cls, '_settings'):
                is_property = lambda x: isinstance(x, property)
                cls._settings = {}
                for name, prop in getmembers(cls, is_property):
                    backing_field = '_' + name
                    if not hasattr(cls, backing_field):
                        backing_field = None
                    cls._settings[name] = (prop, backing_field)
                cls._keys = sorted(cls._settings.iterkeys())
            return cls._settings

        @classmethod
        def fix_up(cls, command_class):
            """ Adjusts and checks this class and its search command class.

            Derived classes must override this method. It is used by the :decorator:`Configuration` decorator to fix up
            the :class:`SearchCommand` classes it adorns. This method is overridden by :class:`GeneratingCommand`,
            :class:`ReportingCommand`, and :class:`StreamingCommand`, the base types for all other search commands.

            :param command_class: Command class targeted by this class

            """
            raise NotImplementedError('SearchCommand.fix_up method must be overridden')

        # endregion

    # endregion
