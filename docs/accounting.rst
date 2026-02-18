==========
Accounting
==========

When running a shared cluster for multiple tenants, you may want to monitor cluster usage
or even compute accountable usage numbers per tenant. There are several ways to achieve
this goal, all with their own benefits and drawbacks.

Working with `events.xml`
=========================

You could use tenant overrides to enforce `meetingKeepEvents=true` during meeting creation,
and then collect and analyse the `events.xml` files from all your BBB servers.

This is the most detailed, but also most invasive approach because `events.xml` contains
WAY more data than necessary, including participant names and chat messages. You should
definitely speak to your *Data Protection Officer* to make sure this is okay. 

You'd also need to parse and process `events.xml` yourself. I'm not aware of any
ready-to-use tool to get usage statistics out of those even streams.


Enabling `ANALYTICS_STORE`
==========================

Similar to the `events.xml` approach you can use tenant overrides to enforce
`meetingKeepEvents=true` during meeting creation, but instead of fetching and parsing raw
`events.xml` files, you enable `ANALYTICS_STORE` in BBBLB and collect analytics data
on your BBBLB server. You will end up with one analytics file per meeting, neatly separated
by tenant.

These analytics JSON files are pre-processed and way easier to parse than raw `events.xml`
files. They no longer contain (private) chats, but you will still find participant names
in there, so don#t forget to speak to your *Data Protection Officer* about it. You also
need to get rid of the `events.xml` files on your BBB servers in a timely manner to not
get in conflict with GDPR.


Enabling `POLL_STATS`
=====================

.. versionadded:: 0.0.17

.. caution::

   This is an experimental feature.


BBBLB can store meeting statistics (namely `users`, `voice` and `video` counts) into the
database, allowing you to run your own queries and analytics. This is disabled by default,
because one database row per meeting per `POLL_INTERVAL` quickly adds up and there is no
automatic cleanup. You'll have to delete old rows yourself and make sure your database can
handle it. On the plus side, those numbers won't contain any personal data, just meeting
IDs and counters.

Once activated with the `POLL_STATS` setting, the meeting poller will store the current
user, voice and video stream count per meeting on each poll. You can write your own SQL
queries to get what you need. A common approach would be to fetch all rows in a certain
time range, calculate average values per meeting, then group those together by tenant.

Here is an (untested) example query that shows most of the techniques:

.. code:: sql

  SELECT
    tenants.name,
    /* Total number of meeting minutes spent by each participant */ 
    SUM(users_avg * EXTRACT(epoch FROM started - ended)) / 60,
    /* Average meeting duration in minutes */ 
    AVG(EXTRACT(epoch FROM started - ended)) / 60, 
    /* Aveage meeting size */ 
    AVG(users_avg),
    /* Maximum meeting size */ 
    MAX(users_max),
    /* Number of meetings with more than 100 users peak */ 
    COUNT(CASE WHEN users_max > 100 THEN 1 END)
    /* Number of meetings */ 
    COUNT(*),
  FROM (
      SELECT
        tenant_fk,
        uuid,
        MIN(ts) AS started,
        MAX(ts) AS ended,
        AVG(users) AS users_avg
        MAX(users) AS users_max
      FROM meeting_stats
      WHERE ts::date <@ '[2027-01-01,2027-02-01)'::daterange 
      GROUP BY tenant_fk, uuid
  )
  INNER JOIN tenants ON (tenant_fk = tenants.id)
  GROUP BY tenants.name
