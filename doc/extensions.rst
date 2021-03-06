.. default-role:: code


Extensions
==========

Pig
---

Since pig jobs are so common, `azkaban` comes with an extension to:

* run pig scripts directly from the command line (and view the output logs 
  from your terminal): `azkabanpig`. Under the hood, this will package your 
  script along with the appropriately generated job file and upload it to 
  Azkaban. Running `azkabanpig --help` displays the list of available options 
  (using UDFs, substituting parameters, running several scripts in order, 
  etc.).

* integrate pig jobs easily into your project configuration via the `PigJob` 
  class. It accepts a file path (to the pig script) as first constructor 
  argument, optionally followed by job options. It then automatically sets the 
  job type and adds the corresponding script file to the project.

  .. code-block:: python

    from azkaban import PigJob

    project.add_job('baz', PigJob('baz.pig', {'dependencies': 'bar'}))


The full API for the `PigJob` class is below.

.. autoclass:: azkaban.ext.pig.PigJob
    :members:
    :show-inheritance:
