==========
Accounting
==========

When running a shared cluster for multiple tenants, you may want to monitor cluster usage
or even compute accountable usage numbers per tenant. There are several ways to achieve
this goal, all with their own benefits and drawbacks.

Parsing raw `events.xml` files
==============================

You could use tenant overrides to enforce `meetingKeepEvents=true` during meeting creation,
and then collect and analyze the `events.xml` files from all your BBB servers.

This is the most detailed, but also most invasive approach because `events.xml` contains
WAY more data than necessary, including participant names and chat messages, even
private chats. You should definitely speak to your *Data Protection Officer* to make
sure this is okay.

You'd also need to parse and process `events.xml` yourself. I'm not aware of any
ready-to-use tool to get usage statistics out of those even streams.


Collecting analytics data
=========================

Similar to the `events.xml` approach you can use tenant overrides to enforce
`meetingKeepEvents=true` during meeting creation, but instead of fetching and parsing raw
`events.xml` files, you enable `ANALYTICS_STORE` in BBBLB and collect analytics data
on your BBBLB server. You will end up with one analytics file per meeting, neatly separated
by tenant.

These analytics JSON files are pre-processed and way easier to parse than raw `events.xml`
files. They no longer contain (private) chats, but you will still find participant names
in there, so don't forget to speak to your *Data Protection Officer* about it. You also
need to get rid of the `events.xml` files on your BBB servers in a timely manner to not
get in conflict with GDPR.


Detailed metrics with `POLL_STATS`
==================================

.. versionadded:: 0.0.17

.. caution::

   This is an experimental feature.


BBBLB can collect detailed metrics and store them in the database, allowing you to run
your own SQL queries to get any statistics you may need. Once activated with the
`POLL_STATS` setting, the meeting poller will store the current `users`, `voice` and
`video` counts for each running meeting on each server poll. Those metrics do not
contain any personal data, which makes this approach very GDPR friendly.

The `POLL_STATS` feature is disabled by default, because the database table will grow by
one row per meeting per `POLL_INTERVAL` and there is no automatic cleanup. This adds up
quickly, especially for large or busy clusters. Make sure to delete old rows regularly
to keep your database size in check.

The `meeting_stats` table is structured similar to a time series database. Each row has
a timestamp (`ts`), the `uuid` of the meeting, the reusable external `meeting_id` that
was used to create the meeting, the owning tenant (`tenant_fk`), and three metric values
named `users`, `voice` and `video`.

The timestamp (`ts`) will be the exact same for all measurements taken during a single
poll interval. It marks the start of the poll interval, not the exact time of an
individual measurement. This is is done on purpose so you can group by the timestamp to
get a consistend view of the entire cluster at a specific time.

Here is an example that calculates user counts for the entire cluster over time.
It uses the fact mentioned above that all measurements taken during a single poll interval
will have the exact same timestamp.

.. code:: sql

    SELECT
      ts,
      COUNT(*) as meetings,
      SUM(users) AS users
    FROM meeting_stats
    GROUP BY ts
    ORDER BY ts

Here is a more complex PostgreSQL example. It fetches all rows in a certain time range,
calculates min/max/avg values per meeting (per `uuid`), then groups those together by
`tenant_fk` to get meaningful aggregated values per tenant.

.. code:: sql

  SELECT
    tenants.name AS tenant,
    /* Number of meetings */ 
    COUNT(*) AS meetings,
    /* Total number of meeting minutes spent by all users combined */ 
    SUM(users_avg * EXTRACT(epoch FROM duration)) / 60 AS meeting_minutes,
    /* Average meeting duration in minutes */ 
    AVG(EXTRACT(epoch FROM duration)) / 60 AS duration_avg, 
    /* Average meeting size */ 
    AVG(users_avg) AS users_avg,
    /* Maximum meeting size */ 
    MAX(users_max) AS users_max,
    /* Number of meetings with more than 25 users peak */ 
    COUNT(CASE WHEN users_max > 100 THEN 1 END) AS large_25,
    /* Number of meetings with more than 100 users peak */ 
    COUNT(CASE WHEN users_max > 100 THEN 1 END) AS large_100
  FROM (
      SELECT
        tenant_fk,
        uuid,
        MAX(ts) - MIN(ts) as duration,
        AVG(users) AS users_avg,
        MAX(users) AS users_max
      FROM meeting_stats
      WHERE ts::date <@ '[2026-02-01,2026-03-01)'::daterange 
      GROUP BY tenant_fk, uuid
  )
  INNER JOIN tenants ON (tenant_fk = tenants.id)
  GROUP BY tenants.name

