{{ fullname | escape | underline }}

.. automodule:: {{ fullname }}

{% if functions %}
Functions
---------

.. autosummary::
   :toctree:
   :nosignatures:
{% for item in functions %}
   {{ item }}
{%- endfor %}
{% endif %}
{% if classes %}
Classes
-------

.. autosummary::
   :toctree:
   :nosignatures:
{% for item in classes %}
   {{ item }}
{%- endfor %}
{% endif %}
{% if exceptions %}
Exceptions
----------

.. autosummary::
   :toctree:
   :nosignatures:
{% for item in exceptions %}
   {{ item }}
{%- endfor %}
{% endif %}
