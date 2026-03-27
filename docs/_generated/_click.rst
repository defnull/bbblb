.. _cli-bbblb:

bbblb
=====

``Usage: bbblb [OPTIONS] COMMAND [ARGS]...``

.. table:: Options
  :width: 100%

  ======================  ==================================================
  Option                  Help                                              
  ======================  ==================================================
  -C, --config-file FILE  Load config from file                             
  -c, --config KEY=VALUE  Set or unset a BBBLB config parameter             
  -v, --verbose           Increase verbosity. Can be repeated.  [default: 0]
  ======================  ==================================================

.. table:: Sub-Commands
  :width: 100%

  ======================================  ======================================================
  Sub-Command                             Help                                                  
  ======================================  ======================================================
  :ref:`db <cli-bbblb-db>`                Manage the database schema                            
  :ref:`maketoken <cli-bbblb-maketoken>`  Generate admin- and other and API tokens.             
  :ref:`recording <cli-bbblb-recording>`  Recording management.                                 
  :ref:`server <cli-bbblb-server>`        Manage BBB servers.                                   
  :ref:`state <cli-bbblb-state>`          Tools to import or export cluster state as JSON files.
  :ref:`tenant <cli-bbblb-tenant>`        Manage tenants.                                       
  ======================================  ======================================================

.. _cli-bbblb-db:

db
--

``Usage: bbblb db [OPTIONS] COMMAND [ARGS]...``

Manage the database schema

.. table:: Sub-Commands
  :width: 100%

  =====================================  ==============================================================
  Sub-Command                            Help                                                          
  =====================================  ==============================================================
  :ref:`migrate <cli-bbblb-db-migrate>`  Migrate the configured database to the current schema version.
  =====================================  ==============================================================

.. _cli-bbblb-db-migrate:

db migrate
^^^^^^^^^^

``Usage: bbblb db migrate [OPTIONS]``

Migrate the configured database to the current schema version.

WARNING: Make backups!

.. table:: Options
  :width: 100%

  ========  ==========================================
  Option    Help                                      
  ========  ==========================================
  --create  Create database if needed (only postgres).
  ========  ==========================================

.. _cli-bbblb-maketoken:

maketoken
---------

``Usage: bbblb maketoken [OPTIONS] SUBJECT [SCOPE]...``

Generate admin- and other and API tokens.

The SUBJECT should be a short name or id that identifies the token
or token owner. It will be logged when the token is used.

SCOPEs limit the capabilities and permissions for this token. If no scope
is defined, the token will have `admin` privileges.

Tenant or Server tokens do not have scopes, their permissions are hard
coded because tenants or servers can create their own tokens.

.. table:: Options
  :width: 100%

  ====================  ======================================================================
  Option                Help                                                                  
  ====================  ======================================================================
  -t, --tenant TEXT     Create a Tenant-Token instead of an Admin-Token.                      
  -s, --server TEXT     Create a Server-Token instead of an Admin-Token.                      
  -e, --expire SECONDS  Number of seconds after which this token should expire.  [default: -1]
  -v, --verbose         Print the clear-text token to stdout.                                 
  SUBJECT               Required argument                                                     
  SCOPE                 Optional argument                                                     
  ====================  ======================================================================

.. _cli-bbblb-recording:

recording
---------

``Usage: bbblb recording [OPTIONS] COMMAND [ARGS]...``

Recording management.

.. table:: Sub-Commands
  :width: 100%

  ==========================================================  ============================================================================
  Sub-Command                                                 Help                                                                        
  ==========================================================  ============================================================================
  :ref:`list <cli-bbblb-recording-list>`                      List all recordings and their formats                                       
  :ref:`delete <cli-bbblb-recording-delete>`                  Delete recordings (all formats)                                             
  :ref:`publish <cli-bbblb-recording-publish>`                Publish recordings                                                          
  :ref:`unpublish <cli-bbblb-recording-unpublish>`            Unpublish recordings                                                        
  :ref:`import <cli-bbblb-recording-import>`                  Import one or more recordings from a tar archive                            
  :ref:`check-database <cli-bbblb-recording-check-database>`  (experimental) Report and optionally fix issues with the recording database.
  ==========================================================  ============================================================================

.. _cli-bbblb-recording-list:

recording list
^^^^^^^^^^^^^^

``Usage: bbblb recording list [OPTIONS]``

List all recordings and their formats

.. table:: Options
  :width: 100%

  =============  ================
  Option         Help            
  =============  ================
  --tenant TEXT  Filter by tenant
  --format TEXT  Filter by format
  =============  ================

.. _cli-bbblb-recording-delete:

recording delete
^^^^^^^^^^^^^^^^

``Usage: bbblb recording delete [OPTIONS] [RECORD_ID]...``

Delete recordings (all formats)

.. table:: Options
  :width: 100%

  =========  =================
  Option     Help             
  =========  =================
  RECORD_ID  Optional argument
  =========  =================

.. _cli-bbblb-recording-publish:

recording publish
^^^^^^^^^^^^^^^^^

``Usage: bbblb recording publish [OPTIONS] [RECORD_ID]...``

Publish recordings

.. table:: Options
  :width: 100%

  =========  =================
  Option     Help             
  =========  =================
  RECORD_ID  Optional argument
  =========  =================

.. _cli-bbblb-recording-unpublish:

recording unpublish
^^^^^^^^^^^^^^^^^^^

``Usage: bbblb recording unpublish [OPTIONS] [RECORD_ID]...``

Unpublish recordings

.. table:: Options
  :width: 100%

  =========  =================
  Option     Help             
  =========  =================
  RECORD_ID  Optional argument
  =========  =================

.. _cli-bbblb-recording-import:

recording import
^^^^^^^^^^^^^^^^

``Usage: bbblb recording import [OPTIONS] [FILE]``

Import one or more recordings from a tar archive

.. table:: Options
  :width: 100%

  =======================  ===========================================
  Option                   Help                                       
  =======================  ===========================================
  --tenant TEXT            Override the tenant found in the recording 
  --publish / --unpublish  Publish or unpublish recording after import
  FILE                     Optional argument                          
  =======================  ===========================================

.. _cli-bbblb-recording-check-database:

recording check-database
^^^^^^^^^^^^^^^^^^^^^^^^

``Usage: bbblb recording check-database [OPTIONS]``

(experimental) Report and optionally fix issues with the recording database.

This command scans the actual recording data found on disk and
checks for missing or inconsistent database entries. It can be used
to repair or rebuild the recordings database after a crash or when
your database backup is missing a few recordings.

Warning, this command may run for a while and consume a lot of memory
if you have many recordings. It is also NOT safe to run this command
while BBBLB is running and processing new recordings. Stop all BBBLB
API and worker processes before starting a scan, especially with
fixes enabled. Make backups!

.. table:: Options
  :width: 100%

  ==============  ============================================================================================
  Option          Help                                                                                        
  ==============  ============================================================================================
  --prefix TEXT   Only scan recording with IDs starting with this prefix.  [default: ""]                      
  --fix-orphans   Remove recordings or formats that do not exist on disk.                                     
  --fix-missing   Import missing recordings or formats found on disk.                                         
  --fix-state     Fix the published/unpublished state of recordings to match the on-disk state.               
  --fix-tenant    Fix the recording owner to match their on-disk storage path, which contains the tenant name.
  --fix-metadata  (NOT IMPLEMENTED) Fix the recording metadata from the most recend on-disk backup.           
  --fix-all       Fix everything that can be fixed automatically.                                             
  ==============  ============================================================================================

.. _cli-bbblb-server:

server
------

``Usage: bbblb server [OPTIONS] COMMAND [ARGS]...``

Manage BBB servers.

.. table:: Sub-Commands
  :width: 100%

  =========================================  ========================================================
  Sub-Command                                Help                                                    
  =========================================  ========================================================
  :ref:`create <cli-bbblb-server-create>`    Create a new server or update a server secret.          
  :ref:`enable <cli-bbblb-server-enable>`    Enable servers and make them available for new meetings.
  :ref:`disable <cli-bbblb-server-disable>`  Disable a server and wait for meetings to end.          
  :ref:`delete <cli-bbblb-server-delete>`    Remove an empty server from the server list.            
  :ref:`list <cli-bbblb-server-list>`        List all servers with their secrets.                    
  :ref:`stats <cli-bbblb-server-stats>`      Show server statistics (state, health, load).           
  =========================================  ========================================================

.. _cli-bbblb-server-create:

server create
^^^^^^^^^^^^^

``Usage: bbblb server create [OPTIONS] DOMAIN``

Create a new server or update a server secret.

.. table:: Options
  :width: 100%

  =============  ===================================================
  Option         Help                                               
  =============  ===================================================
  -U, --update   Update the server with the same domain, if present.
  --secret TEXT  Set the server secret. Required for new servers    
  DOMAIN         Required argument                                  
  =============  ===================================================

.. _cli-bbblb-server-enable:

server enable
^^^^^^^^^^^^^

``Usage: bbblb server enable [OPTIONS] [DOMAINS]...``

Enable servers and make them available for new meetings.

.. table:: Options
  :width: 100%

  =======  ==============================================================================
  Option   Help                                                                          
  =======  ==============================================================================
  DOMAINS  Optional argument                                                             
  --now    Skip health checks and make the server available for new meetings immediately.
  =======  ==============================================================================

.. _cli-bbblb-server-disable:

server disable
^^^^^^^^^^^^^^

``Usage: bbblb server disable [OPTIONS] [DOMAINS]...``

Disable a server and wait for meetings to end.

.. table:: Options
  :width: 100%

  ==============  ===============================================================================
  Option          Help                                                                           
  ==============  ===============================================================================
  DOMAINS         Optional argument                                                              
  --nuke          End all meetings immediately.                                                  
  --wait INTEGER  Seconds to wait for meetings to end. A value of -1 waits forever.  [default: 0]
  ==============  ===============================================================================

.. _cli-bbblb-server-delete:

server delete
^^^^^^^^^^^^^

``Usage: bbblb server delete [OPTIONS] DOMAIN``

Remove an empty server from the server list.

.. table:: Options
  :width: 100%

  ======  =================
  Option  Help             
  ======  =================
  DOMAIN  Required argument
  ======  =================

.. _cli-bbblb-server-list:

server list
^^^^^^^^^^^

``Usage: bbblb server list [OPTIONS]``

List all servers with their secrets.

.. table:: Options
  :width: 100%

  ======================================  ==================================================
  Option                                  Help                                              
  ======================================  ==================================================
  --table-format [simple|plain|raw|json]  Change the result table format.  [default: simple]
  ======================================  ==================================================

.. _cli-bbblb-server-stats:

server stats
^^^^^^^^^^^^

``Usage: bbblb server stats [OPTIONS]``

Show server statistics (state, health, load).

.. table:: Options
  :width: 100%

  ======================================  ==================================================
  Option                                  Help                                              
  ======================================  ==================================================
  --table-format [simple|plain|raw|json]  Change the result table format.  [default: simple]
  ======================================  ==================================================

.. _cli-bbblb-state:

state
-----

``Usage: bbblb state [OPTIONS] COMMAND [ARGS]...``

Tools to import or export cluster state as JSON files.

.. table:: Sub-Commands
  :width: 100%

  ======================================  =========================================================
  Sub-Command                             Help                                                     
  ======================================  =========================================================
  :ref:`export <cli-bbblb-state-export>`  Export current cluster state as JSON.                    
  :ref:`import <cli-bbblb-state-import>`  Load and apply server and tenant configuration from JSON.
  ======================================  =========================================================

.. _cli-bbblb-state-export:

state export
^^^^^^^^^^^^

``Usage: bbblb state export [OPTIONS] [FILE]``

Export current cluster state as JSON.

.. table:: Options
  :width: 100%

  ==================  ============================================================================================
  Option              Help                                                                                        
  ==================  ============================================================================================
  -i, --include LIST  Comma separated list of resource types to include in the export.  [default: servers,tenants]
  FILE                Optional argument                                                                           
  ==================  ============================================================================================

.. _cli-bbblb-state-import:

state import
^^^^^^^^^^^^

``Usage: bbblb state import [OPTIONS] [FILE]``

Load and apply server and tenant configuration from JSON.

WARNING: This will modify or remove tenants and servers without asking.
Try with --dry-run first if you are unsure.

Obsolete servers and tenants are disabled by default.
Use --clean to fully remove them.

Servers and tenants with meetings cannot be removed.
Use --nuke to forcefully end all meetings on obsolete servers or meetings.

.. table:: Options
  :width: 100%

  ==================  =======================================================================================================
  Option              Help                                                                                                   
  ==================  =======================================================================================================
  --nuke              End all meetings related to obsolete servers or tenants                                                
  --delete            Remove obsolete server and tenants instead of just disabling them.Combine with --nuke to force removal.
  -n, --dry-run       Simulate changes without changing anything.                                                            
  -i, --include LIST  Comma separated list of resource types to include in the export.  [default: servers,tenants]           
  FILE                Optional argument                                                                                      
  ==================  =======================================================================================================

.. _cli-bbblb-tenant:

tenant
------

``Usage: bbblb tenant [OPTIONS] COMMAND [ARGS]...``

Manage tenants.

.. table:: Sub-Commands
  :width: 100%

  ===========================================  =========================================
  Sub-Command                                  Help                                     
  ===========================================  =========================================
  :ref:`create <cli-bbblb-tenant-create>`      Create a new tenant.                     
  :ref:`enable <cli-bbblb-tenant-enable>`      Enable a tenant.                         
  :ref:`disable <cli-bbblb-tenant-disable>`    Disable (lock out) a tenant.             
  :ref:`list <cli-bbblb-tenant-list>`          List all tenants and their configuration.
  :ref:`override <cli-bbblb-tenant-override>`  Manage tenant overrides.                 
  ===========================================  =========================================

.. _cli-bbblb-tenant-create:

tenant create
^^^^^^^^^^^^^

``Usage: bbblb tenant create [OPTIONS] NAME``

Create a new tenant.

.. table:: Options
  :width: 100%

  =============  ===============================================================================
  Option         Help                                                                           
  =============  ===============================================================================
  -U, --update   Update the tenant with the same name, if any.                                  
  --realm TEXT   Set tenant realm. Defaults to '{name}.{DOMAIN}' for new tenants.               
  --secret TEXT  Set the tenant secret. Defaults to a randomly generated string for new tenants.
  NAME           Required argument                                                              
  =============  ===============================================================================

.. _cli-bbblb-tenant-enable:

tenant enable
^^^^^^^^^^^^^

``Usage: bbblb tenant enable [OPTIONS] NAME``

Enable a tenant.

.. table:: Options
  :width: 100%

  ======  =================
  Option  Help             
  ======  =================
  NAME    Required argument
  ======  =================

.. _cli-bbblb-tenant-disable:

tenant disable
^^^^^^^^^^^^^^

``Usage: bbblb tenant disable [OPTIONS] NAME``

Disable (lock out) a tenant.

.. table:: Options
  :width: 100%

  ======  ======================================
  Option  Help                                  
  ======  ======================================
  NAME    Required argument                     
  --nuke  End all meetings owned by this tenant.
  ======  ======================================

.. _cli-bbblb-tenant-list:

tenant list
^^^^^^^^^^^

``Usage: bbblb tenant list [OPTIONS]``

List all tenants and their configuration.

.. table:: Options
  :width: 100%

  ======================================  ==================================================
  Option                                  Help                                              
  ======================================  ==================================================
  --table-format [simple|plain|raw|json]  Change the result table format.  [default: simple]
  --with-overrides                        Include overrides in listing.                     
  --with-secret                           Include secret in listing.                        
  ======================================  ==================================================

.. _cli-bbblb-tenant-override:

tenant override
^^^^^^^^^^^^^^^

``Usage: bbblb tenant override [OPTIONS] TENANT``

Manage tenant overrides.

Tenant overrides affect the parameters of `create` or `join` API
calls coming from a tenant.

You can `--set` any number of overrides per tenant as `PARAM=VALUE`
pairs. `PARAM` should match a BBB API parameter supported by the
given type (`create` or `join`) and `VALUE` will be enforced on all
future API calls issued by this tenant. If `VALUE` is empty, then
the parameter will be removed from API calls.

Instead of the `=` operator you can also use `PARAM?VALUE` to define
a fallback for missing parameters, `PARAM<VALUE` to define a maximum
value for numeric parameters (e.g. 'duration' or 'maxParticipants'),
or `PARAM+VALUE` to add items to a list-type parameter
(e.g. 'disabledFeatures').

Example: `--set record=false --set duration<40`

.. table:: Options
  :width: 100%

  ====================  ===================================================================================================
  Option                Help                                                                                               
  ====================  ===================================================================================================
  TENANT                Required argument                                                                                  
  --type [create|join]  Change the API call this override should apply to  [default: create]                               
  --set PARAM=VALUE     Set or replace an override for a specific API parameter. Can be repeated for additional parameters.
  --unset PARAM         Remove an override for a specific API parameter. Can be repeated for additional parameters.        
  --clear               Remove all overrides before adding new ones.                                                       
  ====================  ===================================================================================================

