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
from . search_command import SearchCommand
from cStringIO import StringIO

import csv


class GeneratingCommand(SearchCommand):
    """ Generates events based on command arguments.

    Generating commands receive no input and must be the first command on a
    pipeline. By default Splunk will run your command locally on a search head:

    .. code-block:: python

        @Configuration()
        class SomeGeneratingCommand(GeneratingCommand)

    You can change the default behavior by configuring your generating command
    for event streaming:

    .. code-block:: python

        @Configuration(streaming=True)
        class SomeGeneratingCommand(GeneratingCommand)
            ...

    Splunk will then run your command locally on a search head and/or remotely
    on one or more indexers.

    You can tell Splunk to run your streaming-enabled generating command locally
    on a search head, never remotely on indexers:

    .. code-block:: python

        @Configuration(local=True, streaming=True)
        class SomeGeneratingCommand(GeneratingCommand)
            ...

    If your generating command produces event records in time order, you must
    tell Splunk to ensure correct behavior:

    .. code-block:: python

        @Configuration(generates_timeorder=True)
        class SomeGeneratingCommand(GeneratingCommand)
            ...

    :ivar input_header: :class:`InputHeader`:  Collection representing the input
        header associated with this command invocation.

    :ivar messages: :class:`MessagesHeader`: Collection representing the output
        messages header associated with this command invocation.

    """
    # region Methods

    def generate(self):
        """ A generator that yields records to the Splunk processing pipeline

        You must override this method.

        """
        raise NotImplementedError('GeneratingCommand.generate(self)')

    def _execute(self, ifile, ofile):
        """ Execution loop

        :param ifile: Input file object. Unused.
        :type ifile: file

        :param ofile: Output file object.
        :type ofile: file

        :return: `None`.

        """
        while True:
            output_buffer = StringIO()

            # TODO: Ensure support for multi-valued fields

            writer = csv.writer(output_buffer, dialect='splunklib.searchcommands')
            record_count = 0L

            for record in self.generate():
                writer.writerow(record)
                record_count += 1L

            # TODO: Write metadata produced by the command, not the metadata read by the command
            self._write_chunk(ofile, None, output_buffer.getvalue())
            pass

    # endregion

    # region Types

    class ConfigurationSettings(SearchCommand.ConfigurationSettings):
        """ Represents the configuration settings for a
        :code:`GeneratingCommand` class

        """
        # region Properties

        @property
        def type(self):
            """ Command type.

            Fixed: :const:`'generating'`

            """
            return True

        # endregion

        # region Methods

        @classmethod
        def fix_up(cls, command):
            """ Verifies :code:`command` class structure.

            """
            if command.generate == GeneratingCommand.generate:
                raise AttributeError('No GeneratingCommand.generate override')
            return

        # endregion

    # endregion
