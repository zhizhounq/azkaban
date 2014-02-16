#!/usr/bin/env python
# encoding: utf-8

"""Azkaban session module.

This contains the `Session` class which will be used for all interactions with
a remote Azkaban server.

"""


from getpass import getpass, getuser
from os.path import exists
from .util import AzkabanError, azkaban_request, extract_json
import logging


logger = logging.getLogger(__name__)


class Session(object):

  """Azkaban session.

  :param url: http endpoint (including protocol, port and optional user)
  :param session_id: optional session id (if valid this will avoid having to
    log in)

  """

  def __init__(self, url, session_id=None):
    parsed_url = url.rstrip('/').split('@')
    parsed_url_length = len(parsed_url)
    if parsed_url_length == 1:
      user = getuser()
      url = parsed_url[0]
    elif parsed_url_length == 2:
      user = parsed_url[0]
      url = parsed_url[1]
    else:
      raise AzkabanError('Malformed url: %r' % (url, ))
    self.id = session_id
    self.url = url
    self.user = user
    logger.debug('session %s@%s instantiated', user, url)

  def _refresh(self, password=None):
    """Refresh session ID.

    :param password: password used to log into Azkaban (only used if no alias
      is provided). can be set to `False` to fail instead of prompting for a
      password.

    """
    logger.debug('refreshing session')
    password = password or getpass(
      'Azkaban password for %s@%s: ' % (self.user, self.url)
    )
    res = extract_json(azkaban_request(
      'POST',
      self.url,
      data={'action': 'login', 'username': self.user, 'password': password},
    ))
    self.id = res['session.id']

  def _request(self, method, endpoint, use_cookies=True, attempts=1, **kwargs):
    """Make request to Azkaban using this session.

    :param method: http method
    :param endpoint: server endpoint (e.g. manager)
    :param attempts: if current session ID is invalid, maximum number of
      attempts to refresh it
    :param use_cookies: include session_id in cookies instead of request data
    :param **kwargs: keyword arguments passed to `azkaban.util.azkaban_request`

    If the session expired, will prompt for a password to refresh.

    """
    full_url = '%s/%s' % (self.url, endpoint.lstrip('/'))
    logger.debug('sending request to %r: %r', full_url, kwargs)
    while True:
      if use_cookies:
        kwargs.setdefault('cookies', {})['azkaban.browser.session.id'] = self.id
      else:
        kwargs.setdefault('data', {})['session.id'] = self.id
      res = azkaban_request(method, full_url, **kwargs) if self.id else None
      if res is None or '<!-- /.login -->' in res.text:
        # checking for None because 500 responses evaluate to False
        logger.debug('request failed because of invalid login')
        if attempts > 0:
          self._refresh()
          attempts -= 1
        else:
          raise AzkabanError(
            'Too many unsuccessful login attempts for url %r. Aborting.',
            self.url
          )
      else:
        return res

  def get_execution_status(self, exec_id):
    """Get status of an execution.

    :param exec_id: execution ID

    """
    return extract_json(self._request(
      method='GET',
      endpoint='executor',
      params={
        'execid': exec_id,
        'ajax': 'fetchexecflow',
      },
    ))

  def get_job_logs(self, exec_id, job, offset=0, limit=50000):
    """Get logs from a job execution.

    :param exec_id: execution ID
    :param job: job name
    :param offset: log offset
    :param limit: size of log to download

    """
    return extract_json(self._request(
      method='GET',
      endpoint='executor',
      params={
        'execid': exec_id,
        'jobId': job,
        'ajax': 'fetchExecJobLogs',
        'offset': offset,
        'length': limit,
      },
    ))

  def cancel_execution(self, exec_id):
    """Cancel workflow execution.

    :param exec_id: execution ID

    """
    res = extract_json(self._request(
      method='GET',
      endpoint='executor',
      params={
        'execid': exec_id,
        'ajax': 'cancelFlow',
      },
    ))
    if 'error' in res:
      raise AzkabanError('Execution %s is not running.' % (exec_id, ))
    return res

  def create_project(self, name, description):
    """Create project.

    :param name: project name
    :param description: project description

    """
    return extract_json(self._request(
      method='POST',
      endpoint='manager',
      data={
        'action': 'create',
        'name': name,
        'description': description,
      },
    ))

  def delete_project(self, name):
    """Delete a project on Azkaban.

    :param session: `azkaban.util.Session` object

    """
    res = self._request(
      method='GET',
      endpoint='manager',
      params={
        'project': name,
        'delete': 'true',
      },
    )
    msg = "Project '%s' was successfully deleted" % (name, )
    if not msg in res.text:
      raise AzkabanError('Delete failed. Check permissions and existence.')
    return res

  def run_workflow(self, project, flow, jobs=None, block=False):
    """Launch a workflow.

    :param project: name of the project
    :param flow: name of the workflow
    :param jobs: name of jobs to run (run entire workflow by default)
    :param block: don't run if the same workflow is already running

    Note that in order to run a workflow on Azkaban, it must already have been
    uploaded and the corresponding user must have permissions to run.

    """
    if not jobs:
      disabled = '[]'
    else:
      all_names = set(
        n['id']
        for n in self.get_workflow_info(project, flow)['nodes']
      )
      run_names = set(jobs)
      missing_names = run_names - all_names
      if missing_names:
        raise AzkabanError(
          'Jobs not found in flow %r: %s.' %
          (flow, ', '.join(missing_names))
        )
      else:
        disabled = (
          '[%s]'
          % (','.join('"%s"' % (n, ) for n in all_names - run_names), )
        )
    return extract_json(self._request(
      method='POST',
      endpoint='executor',
      use_cookies=False,
      data={
        'ajax': 'executeFlow',
        'project': project,
        'flow': flow,
        'disabled': disabled,
        'concurrentOption': 'skip' if block else 'concurrent',
      },
    ))

  def upload_project(self, project, path):
    """Upload project archive.

    :param project: project name
    :param path: path to zip archive

    """
    if not exists(path):
      raise AzkabanError('Unable to find archive at %r.' % (path, ))
    return extract_json(self._request(
      method='POST',
      endpoint='manager',
      use_cookies=False,
      data={
        'ajax': 'upload',
        'project': project,
      },
      files={
        'file': ('file.zip', open(path, 'rb'), 'application/zip'),
      },
    ))

  def get_workflow_info(self, project, flow):
    """Get list of jobs corresponding to a workflow.

    :param project: project name
    :param flow: name of flow in project

    """
    raw_res = self._request(
      method='GET',
      endpoint='manager',
      params={
        'ajax': 'fetchflowjobs',
        'project': project,
        'flow': flow,
      },
    )
    try:
      return extract_json(raw_res)
    except ValueError:
      raise AzkabanError('Flow %r not found.' % (flow, ))
