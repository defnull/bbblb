==========
Accounting
==========

When running a shared cluster for multiple tenants, you may want to monitor cluster usage
or even compute accountable usage numbers per tenant. There are several ways to achieve
this goal, all with their own benefits and drawbacks.

Parsing raw `events.xml` files
==============================

You could use tenant overrides to enforce `meetingKeepEvents=true` during meeting creation,
and then collect and analyse the `events.xml` files from all your BBB servers.

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
in there, so don#t forget to speak to your *Data Protection Officer* about it. You also
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
a timestamp (`ts`), the `uuid` of the meeting, the reuseable external `meeting_id` that
was used to create the meeting, the owning tenant (`tenant_fk`), and three metric values
named `users`, `voice` and `video`.

Here is an (untested) example PostgreSQL query returning some useful aggregations. It
fetches all rows in a certain time range, calculates min/max/avg values per meeting
(per `uuid`), then groups those together by `tenant_fk` to get meaningfull aggregated
values per tenant:

.. code:: sql

  SELECT
    tenants.name,
    /* Total number of meeting minutes spent by all users combined */ 
    SUM(users_avg * EXTRACT(epoch FROM duration)) / 60,
    /* Average meeting duration in minutes */ 
    AVG(EXTRACT(epoch FROM duration)) / 60, 
    /* Aveage meeting size */ 
    AVG(users_avg),
    /* Maximum meeting size */ 
    MAX(users_max),
    /* Number of meetings with more than 100 users peak */ 
    COUNT(CASE WHEN users_max > 100 THEN 1 END),
    /* Number of meetings */ 
    COUNT(*)
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

