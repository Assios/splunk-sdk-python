#!/usr/bin/env python
# coding=utf-8
#
# Copyright © 2011-2015 Splunk, Inc.
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

from splunklib.searchcommands.internals import MetadataDecoder, MetadataEncoder, Recorder, RecordWriter
from splunklib.searchcommands import SearchMetric
from collections import deque, OrderedDict
from cStringIO import StringIO
from functools import wraps
from glob import iglob
from itertools import chain, ifilter, imap, izip
from sys import float_info, maxsize, maxunicode
from tempfile import mktemp
from time import time
from types import MethodType

import cPickle as pickle
import json
import os
import random
import unittest

# region Functions for producing random data

# Confirmed: [minint, maxint) covers the full range of values that xrange allows

minint = (-maxsize - 1) // 2
maxint = maxsize // 2

max_length = 1 * 1024

def random_bytes():
    return os.urandom(random.randint(0, max_length))


def random_dict():

    # We do not call random_bytes because the JSONDecoder raises this UnicodeDecodeError when it encounters
    # bytes outside the UTF-8 character set:
    #
    #   'utf8' codec can't decode byte 0x8d in position 2: invalid start byte
    #
    # One might be tempted to select an alternative encoding, but picking one that works for all bytes is a
    # lost cause. The burden is on the customer to ensure that the strings in the dictionaries they serialize
    # contain utf-8 encoded byte strings or--better still--unicode strings. This is because the json package
    # converts all bytes strings to unicode strings before serializing them.

    return {'a': random_float(), 'b': random_unicode(), '福 酒吧': {'fu': random_float(), 'bar': random_float()}}


def random_float():
    return random.uniform(float_info.min, float_info.max)


def random_integer():
    return random.uniform(minint, maxint)


def random_integers():
    return random_list(xrange, minint, maxint)


def random_list(population, *args):
    return random.sample(population(*args), random.randint(0, max_length))


def random_unicode():
    return ''.join(imap(lambda x: unichr(x), random.sample(xrange(maxunicode), random.randint(0, max_length))))

# endregion


class TestInternals(unittest.TestCase):

    def setUp(self):
        unittest.TestCase.setUp(self)

    def test_object_view(self):

        decoder = MetadataDecoder()
        view = decoder.decode(self._json_input)

        encoder = MetadataEncoder()
        json_output = encoder.encode(view)

        self.assertEqual(self._json_input, json_output)
        return

    def test_recorder(self):

        # Grab an input/output recording, the results of a prior countmatches run

        recording = os.path.join(self._package_path, 'recordings', 'v2', 'Splunk-6.3', 'countmatches.')

        with open(recording + 'input', 'rb') as file_1, open(recording + 'output', 'rb') as file_2:
            ifile = StringIO(file_1.read())
            result = StringIO(file_2.read())

        # Set up the input/output recorders that are under test

        ifile = Recorder(mktemp(), ifile)

        try:
            ofile = Recorder(mktemp(), StringIO())

            try:
                # Read and then write a line
                ifile.readline()
                ofile.write(result.readline())

                # Read and then write a block
                ifile.read()
                ofile.write(result.read())

                # Verify that what we wrote is equivalent to the original recording, the result from a prior
                # countmatches run
                self.assertEqual(ofile.getvalue(), result.getvalue())

                # Verify that we faithfully recorded the input and output files
                with open(ifile._recording.name, 'rb') as file_1, open(ofile._recording.name, 'rb') as file_2:
                    self.assertEqual(file_1.read(), ifile._file.getvalue())
                    self.assertEqual(file_2.read(), ofile._file.getvalue())

            finally:
                ofile._recording.close()
                os.remove(ofile._recording.name)

        finally:
            ifile._recording.close()
            os.remove(ifile._recording.name)

        return

    def test_record_writer_with_random_data(self, record=False):

        # Confirmed: [minint, maxint) covers the full range of values that xrange allows

        # RecordWriter writes data in units of maxresultrows records. Default: 50,0000.
        # Partial results are written when the record count reaches maxresultrows.

        writer = RecordWriter(StringIO(), maxresultrows=10)  # small for the purposes of this unit test
        test_data = OrderedDict()

        fieldnames = ['_serial', '_time', 'random_bytes', 'random_dict', 'random_integers', 'random_unicode']
        test_data['fieldnames'] = fieldnames
        test_data['values'] = []

        write_record = writer.write_record

        for serial_number in xrange(0, 31):
            values = [serial_number, time(), random_bytes(), random_dict(), random_integers(), random_unicode()]
            record = OrderedDict(izip(fieldnames, values))
            try:
                write_record(record)
            except Exception as error:
                self.fail(error)
            test_data['values'].append(values)

        # RecordWriter accumulates inspector messages and metrics until maxresultrows are written, a partial result
        # is produced or we're finished

        messages = [
            ('debug', random_unicode()),
            ('error', random_unicode()),
            ('fatal', random_unicode()),
            ('info', random_unicode()),
            ('warn', random_unicode())]

        test_data['messages'] = messages

        for message_type, message_text in messages:
            writer.write_message(message_type, '{}', message_text)

        metrics = {
            'metric-1': SearchMetric(1, 2, 3, 4),
            'metric-2': SearchMetric(5, 6, 7, 8)
        }

        test_data['metrics'] = metrics

        for name, metric in metrics.iteritems():
            writer.write_metric(name, metric)

        self.assertEqual(writer._chunk_count, 3)
        self.assertEqual(writer._record_count, 1)
        self.assertGreater(writer._buffer.tell(), 0)
        self.assertEqual(writer._total_record_count, 30)
        self.assertListEqual(writer._fieldnames, fieldnames)
        self.assertListEqual(writer._inspector['messages'], messages)

        self.assertDictEqual(
            dict(ifilter(lambda (k, v): k.startswith('metric.'), writer._inspector.iteritems())),
            dict(imap(lambda (k, v): ('metric.' + k, v), metrics.iteritems())))

        writer.flush(finished=True)

        self.assertEqual(writer._chunk_count, 4)
        self.assertEqual(writer._record_count, 0)
        self.assertEqual(writer._buffer.tell(), 0)
        self.assertEqual(writer._buffer.getvalue(), '')
        self.assertEqual(writer._total_record_count, 31)

        self.assertRaises(RuntimeError, writer.write_record, {})
        self.assertRaises(RuntimeError, writer.flush)
        self.assertFalse(writer._ofile.closed)
        self.assertIsNone(writer._fieldnames)
        self.assertDictEqual(writer._inspector, OrderedDict())

        # P2 [ ] TODO: Verify that RecordWriter gives consumers the ability to write partial results by calling
        # RecordWriter.flush(partial=True).

        # P2 [ ] TODO: Verify that RecordWriter gives consumers the ability to finish early by calling
        # RecordWriter.flush(finish=True).

        if record:

            cls = self.__class__
            method = cls.test_record_writer_with_recordings
            base_path = os.path.join(self._recordings_path, '.'.join((cls.__name__, method.__name__, unicode(time()))))

            with open(base_path + '.input', 'wb') as f:
                pickle.dump(test_data, f)

            with open(base_path + '.output', 'wb') as f:
                f.write(writer._ofile.getvalue())

        return

    def test_record_writer_with_recordings(self):

        cls = self.__class__
        method = cls.test_record_writer_with_recordings
        base_path = os.path.join(self._recordings_path, '.'.join((cls.__name__, method.__name__)))

        for input_file in iglob(base_path + '*.input'):

            with open(input_file, 'rb') as f:
                test_data = pickle.load(f)

            writer = RecordWriter(StringIO(), maxresultrows=10)  # small for the purposes of this unit test
            write_record = writer.write_record
            fieldnames = test_data['fieldnames']

            for values in test_data['values']:
                record = OrderedDict(izip(fieldnames, values))
                try:
                    write_record(record)
                except Exception as error:
                    self.fail(error)

            for message_type, message_text in test_data['messages']:
                writer.write_message(message_type, '{}', message_text)

            for name, metric in test_data['metrics'].iteritems():
                writer.write_metric(name, metric)

            writer.flush(finished=True)

            with open(os.path.splitext(input_file)[0] + '.output', 'rb') as f:
                expected = f.read()

            self.assertEqual(writer._ofile.getvalue(), expected)

        return

    _dictionary = {
        'a': 1,
        'b': 2,
        'c': {
            'd': 3,
            'e': 4,
            'f': {
                'g': 5,
                'h': 6,
                'i': 7
            },
            'j': 8,
            'k': 9
        },
        'l': 10,
        'm': 11,
        'n': 12
    }

    _json_input = unicode(json.dumps(_dictionary, separators=(',', ':')))
    _package_path = os.path.dirname(os.path.abspath(__file__))
    _recordings_path = os.path.join(_package_path, 'recordings', 'v2', 'Splunk-6.3')


class TestRecorder(object):

    def __init__(self, test_case):

        self._test_case = test_case
        self._output = None
        self._recording = None
        self._recording_part = None

        def _not_implemented(self):
            raise NotImplementedError('class {} is not in playback or record mode'.format(self.__class__.__name__))

        self.get = self.next_part = self.stop = MethodType(_not_implemented, self, self.__class__)
        return

    @property
    def output(self):
        return self._output

    def playback(self, path):

        with open(path, 'rb') as f:
            test_data = pickle.load(f)

        self._output = StringIO()
        self._recording = test_data['inputs']
        self._recording_part = self._recording.popleft()

        def get(self, method, *args, **kwargs):
            return self._recording_part[method.__name__].popleft()

        self.get = MethodType(get, self, self.__class__)

        def next_part(self):
            self._recording_part = self._recording.popleft()

        self.next_part = MethodType(next_part, self, self.__class__)

        def stop(self):
            self._test_case.assertEqual(test_data['results'], self._output.getvalue())

        self.stop = MethodType(stop, self, self.__class__)
        return

    def record(self, path):

        self._output = StringIO()
        self._recording = deque()
        self._recording_part = OrderedDict()
        self._recording.append(self._recording_part)

        def get(self, method, *args, **kwargs):
            result = method(*args, **kwargs)
            part = self._recording_part
            key = method.__name__
            try:
                results = part[key]
            except KeyError:
                part[key] = results = deque()
            results.append(result)
            return result

        self.get = MethodType(get, self, self.__class__)

        def next_part(self):
            part = OrderedDict()
            self._recording_part = part
            self._recording.append(part)

        self.next_part = MethodType(next_part, self, self.__class__)

        def stop(self):
            with open(path, 'wb') as f:
                test = OrderedDict((('inputs', self._recording), ('results', self._output.getvalue())))
                pickle.dump(test, f)

        self.stop = MethodType(stop, self, self.__class__)
        return


def recorded(method):

    @wraps(method)
    def _record(*args, **kwargs):
        return args[0].recorder.get(method, *args, **kwargs)

    return _record


class Test(unittest.TestCase):

    def __init__(self, fieldnames, data_generators):

        unittest.TestCase.__init__(self)

        self._data_generators = list(chain((lambda: self._serial_number, time), data_generators))
        self._fieldnames = list(chain(('_serial', '_time'), fieldnames))
        self._recorder = TestRecorder(self)
        self._serial_number = None

    @property
    @recorded
    def fieldnames(self):
        return self._fieldnames

    @property
    @recorded
    def row(self):
        return [data_generator.__call__() for data_generator in self._data_generators]

    @property
    def recorder(self):
        return self._recorder

    @property
    def serial_number(self):
        return self._serial_number

    def playback(self):
        self.recorder.playback(os.path.join(TestInternals._package_path, 'TestRecorder.recording'))
        self._run()
        self.recorder.stop()

    def record(self):
        self.recorder.record(os.path.join(TestInternals._package_path, 'TestRecorder.recording'))
        self._run()
        self.recorder.stop()

    def runTest(self):
        pass  # We'll adopt the new test recording mechanism a little later

    def _run(self):

        writer = RecordWriter(self.recorder.output, maxresultrows=10)
        write_record = writer.write_record
        names = self.fieldnames

        for self._serial_number in xrange(0, 31):
            record = OrderedDict(izip(names, self.row))
            write_record(record)

        return


# test = Test(['random_bytes', 'random_unicode'], [random_bytes, random_unicode])
# test.record()
# test.playback()

if __name__ == "__main__":
    unittest.main()
