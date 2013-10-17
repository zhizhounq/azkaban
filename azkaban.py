#!/usr/bin/env python
# encoding: utf-8

"""Azkaban CLI.

Usage:
  python FILE upload (-p PROFILE | [-u USER] URL)
  python FILE build PATH
  python FILE view
  python FILE -h | --help | -v | --version

Arguments:
  FILE                          Jobs file.
  PATH                          Output path where zip file will be created.
  URL                           Azkaban endpoint (with port).

Options:
  -h --help                     Show this message and exit.
  -p PROFILE --profile=PROFILE  Saved username, URL. Will also try to reuse
                                session IDs.
  -u USER --user=USER           Username used to log into Azkaban.
  -v --version                  Show version and exit.

"""

from ConfigParser import NoOptionError, RawConfigParser
from contextlib import contextmanager
from docopt import docopt
from getpass import getpass, getuser
from os import close, remove
from os.path import basename, exists, expanduser, isabs, join, splitext
from requests import post
from sys import argv
from tempfile import mkstemp
from zipfile import ZipFile


__version__ = '0.0.1'


def flatten(dct, sep='.'):
  """Flatten a nested dictionary.

  :param dct: dictionary to flatten.
  :param sep: separator used when concatenating keys.

  """
  def _flatten(dct, prefix=''):
    """Inner recursive function."""
    items = []
    for key, value in dct.items():
      new_prefix = '%s%s%s' % (prefix, sep, key) if prefix else key
      if isinstance(value, dict):
        items.extend(_flatten(value, new_prefix).items())
      else:
        items.append((new_prefix, value))
    return dict(items)
  return _flatten(dct)

@contextmanager
def temppath():
  """Create a temporary filepath.

  Usage::

    with temppath() as path:
      # do stuff

  Any file corresponding to the path will be automatically deleted afterwards.

  """
  (desc, path) = mkstemp()
  close(desc)
  remove(path)
  try:
    yield path
  finally:
    if exists(path):
      remove(path)


class AzkabanError(Exception):

  """Base error class."""


class Project(object):

  """Azkaban project.

  :param name: name of the project

  """

  def __init__(self, name):
    self.name = name
    self._jobs = {}
    self._files = {}

  def add_file(self, path, archive_path=None):
    """Include a file in the project archive.

    :param path: absolute path to file
    :param archive_path: path to file in archive (defaults to same as `path`)

    This method requires the path to be absolute to avoid having files in the
    archive with lower level destinations than the base root directory.

    """
    if not isabs(path):
      raise AzkabanError('relative path not allowed: %r' % (path, ))
    elif path in self._files:
      if self._files[path] != archive_path:
        raise AzkabanError('inconsistent duplicate: %r' % (path, ))
    else:
      if not exists(path):
        raise AzkabanError('file missing: %r' % (path, ))
      self._files[path] = archive_path

  def add_job(self, name, job):
    """Include a job in the project.

    :param name: name assigned to job (must be unique)
    :param job: `Job` subclass

    This method triggers the `on_add` method on the added job (passing the
    project and name as arguments). The handler will be called right after the
    job is added.

    """
    if name in self._jobs:
      raise AzkabanError('duplicate job name: %r' % (name, ))
    else:
      self._jobs[name] = job
      job.on_add(self, name)

  def build(self, path):
    """Create the project archive.

    :param path: destination path

    Triggers the `on_build` method on each job inside the project (passing
    itself and the job's name as two argument). This method will be called
    right before the job file is generated.

    """
    # not using a with statement for compatibility with older python versions
    writer = ZipFile(path, 'w')
    try:
      for name, job in self._jobs.items():
        job.on_build(self, name)
        with temppath() as fpath:
          job.generate(fpath)
          writer.write(fpath, '%s.job' % (name, ))
      for fpath, apath in self._files.items():
        writer.write(fpath, apath)
    finally:
      writer.close()

  def upload(self, url, user=None, session_id=None):
    """Build and upload project to Azkaban.

    :param url: http endpoint (including port)
    :param user: username which will be used to upload the built project
      (defaults to the current user)

    """
    user = user or getuser()
    with temppath() as path:
      self.build(path)
      if not session_id:
        # TODO: check if session.id is valid also
        req = post(
          url,
          data={'action': 'login', 'username': user, 'password': getpass()},
          verify=False
        )
        res = req.json()
        if 'error' in res:
          raise AzkabanError(res['error'])
        else:
          session_id = res['session.id']
      req = post(
        '%s/manager' % (url, ),
        data={
          'ajax': 'upload',
          'session.id': session_id,
          'project': self.name,
          'file': path
        },
        verify=False
      )
      if 'error' in req.json():
        raise AzkabanError(res['error'])

  def run(self):
    """TODO: Command line interface."""
    argv.insert(0, 'FILE')
    args = docopt(__doc__, version=__version__)
    if args['build']:
      self.build(args['PATH'])
    elif args['upload']:
      self.upload(args['URL'])
    elif args['view']:
      for name in sorted(self._jobs):
        print name

  def _get_profile(self, profile):
    """Get username, URL and session ID corresponding to profile.

    :param profile: name of profile.

    """
    parser = RawConfigParser()
    parser.read(expanduser('~/.azkabanrc'))
    if not parser.has_section(profile):
      raise AzkabanError('missing profile: %r' % (profile, ))
    elif not parser.has_option(profile, 'url'):
      raise AzkabanError('missing URL for profile: %r' % (profile, ))
    else:
      return {
        'user': parser.get(profile, 'user'),
        'url': parser.get(profile, 'url'),
        'session_id': parser.get(profile, 'session_id'),
      }


class Job(object):

  """Base Azkaban job.

  :param options: list of dictionaries (earlier values take precedence).

  To enable more functionality, subclass and override the `on_add` and
  `on_build` methods.

  """

  def __init__(self, *options):
    self._options = options

  @property
  def options(self):
    """Combined job options."""
    options = {}
    for option in reversed(self._options):
      options.update(flatten(option))
    return options

  def generate(self, path):
    """Create job file.

    :param path: path where job file will be created. Any existing file will
      be overwritten.

    """
    with open(path, 'w') as writer:
      for key, value in sorted(self.options.items()):
        writer.write('%s=%s\n' % (key, value))

  def on_add(self, project, name):
    """Handler called when the job is added to a project.

    :param project: project instance
    :param name: name corresponding to this job in the project.

    """
    pass

  def on_build(self, project, name):
    """Handler called when a project including this job is built.

    :param project: project instance
    :param name: name corresponding to this job in the project.

    """
    pass


class PigJob(Job):

  """Job class corresponding to pig jobs.

  :param path: path to pig script
  :param *options: cf. `Job`

  Implements helpful handlers. To use custom pig type jobs, override the `type`
  class attribute.

  TODO: automatic dependency detection using variables.

  """

  type = 'pig'

  def __init__(self, path, *options):
    if not exists(path):
      raise AzkabanError('pig script missing: %r' % (path, ))
    super(PigJob, self).__init__(
      {'type': self.type, 'pig.script': path},
      *options
    )
    self.path = path

  def on_add(self, project, name):
    """Adds script file to project."""
    project.add_file(self.path)
