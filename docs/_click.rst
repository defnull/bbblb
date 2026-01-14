bbblb
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

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

  =========  ===============================================================================
  Command    Help                                                                           
  =========  ===============================================================================
  db         Manage database                                                                
  maketoken  Generate an Admin Token that can be used to authenticate against the BBBLB API.
  override   Manage tenant overrides                                                        
  recording  Recording management.                                                          
  server     Manage servers                                                                 
  state      Tools to export and import cluster state in JSON files.                        
  tenant     Manage tenants                                                                 
  =========  ===============================================================================

db
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb db [OPTIONS] COMMAND [ARGS]...``

Manage database

.. table:: Sub-Commands
  :width: 100%

  =======  ===============================================
  Command  Help                                           
  =======  ===============================================
  migrate  Migrate database to the current schema version.
  =======  ===============================================

db migrate
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb db migrate [OPTIONS]``

Migrate database to the current schema version.

WARNING: Make backups!

.. table:: Options
  :width: 100%

  ========  ==========================================
  Option    Help                                      
  ========  ==========================================
  --create  Create database if needed (only postgres).
  ========  ==========================================

maketoken
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb maketoken [OPTIONS] SUBJECT [SCOPE]...``

Generate an Admin Token that can be used to authenticate against the BBBLB API.

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

override
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb override [OPTIONS] COMMAND [ARGS]...``

Manage tenant overrides

.. table:: Sub-Commands
  :width: 100%

  =======  ===========================================================
  Command  Help                                                       
  =======  ===========================================================
  list     List create or join overrides by tenant.                   
  set      Override create or join call parameters for a given tenant.
  unset    Remove specific overrides on a tenant.                     
  =======  ===========================================================

override list
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb override list [OPTIONS] [TENANT]``

List create or join overrides by tenant.

.. table:: Options
  :width: 100%

  ===========  =========================================================
  Option       Help                                                     
  ===========  =========================================================
  TENANT       Optional argument                                        
  --type LIST  List specific override types only  [default: create,join]
  ===========  =========================================================

override set
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb override set [OPTIONS] TENANT {create|join} NAME=VALUE``

Override create or join call parameters for a given tenant.

You can define any number of overrides per tenant as PARAM=VALUE
pairs. PARAM should match a BBB API parameter supported by the given
type (create or join) and the given VALUE will be enforced on all
future API calls issued by this tenant. If VALUE is empty, then the
parameter will be removed from API calls.

Instead of the '=' operator you can also use '?' to define a
fallback for missing parameters instead of an override, '<' to
define a maximum value for numeric parameters (e.g. duration
or maxParticipants), or '+' to add items to a comma separated list
parameter (e.g. disabledFeatures).

.. table:: Options
  :width: 100%

  ==========  =====================================================================
  Option      Help                                                                 
  ==========  =====================================================================
  --clear     Remove all overrides for that tenant and type before adding new ones.
  TENANT      Required argument                                                    
  TYPE        Required argument                                                    
  NAME=VALUE  Optional argument                                                    
  ==========  =====================================================================

override unset
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb override unset [OPTIONS] TENANT {create|join} NAME``

Remove specific overrides on a tenant.

.. table:: Options
  :width: 100%

  ======  =================
  Option  Help             
  ======  =================
  TENANT  Required argument
  TYPE    Required argument
  NAME    Optional argument
  ======  =================

recording
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb recording [OPTIONS] COMMAND [ARGS]...``

Recording management.

.. table:: Sub-Commands
  :width: 100%

  ==============  ======================================================
  Command         Help                                                  
  ==============  ======================================================
  list            List all recordings and their formats                 
  delete          Delete recordings (all formats)                       
  publish         Publish recordings                                    
  unpublish       Unpublish recordings                                  
  import          Import one or more recordings from a tar archive      
  remove-orphans  Remove recording DB entries that do not exist on disk.
  ==============  ======================================================

recording list
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb recording list [OPTIONS]``

List all recordings and their formats

recording delete
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb recording delete [OPTIONS] [RECORD_ID]...``

Delete recordings (all formats)

.. table:: Options
  :width: 100%

  =========  =================
  Option     Help             
  =========  =================
  RECORD_ID  Optional argument
  =========  =================

recording publish
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb recording publish [OPTIONS] [RECORD_ID]...``

Publish recordings

.. table:: Options
  :width: 100%

  =========  =================
  Option     Help             
  =========  =================
  RECORD_ID  Optional argument
  =========  =================

recording unpublish
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb recording unpublish [OPTIONS] [RECORD_ID]...``

Unpublish recordings

.. table:: Options
  :width: 100%

  =========  =================
  Option     Help             
  =========  =================
  RECORD_ID  Optional argument
  =========  =================

recording import
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

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

recording remove-orphans
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb recording remove-orphans [OPTIONS]``

Remove recording DB entries that do not exist on disk.

.. table:: Options
  :width: 100%

  =============  ======================================
  Option         Help                                  
  =============  ======================================
  -n, --dry-run  Do not actually remove any recordings.
  =============  ======================================

server
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb server [OPTIONS] COMMAND [ARGS]...``

Manage servers

.. table:: Sub-Commands
  :width: 100%

  =======  =======================================================
  Command  Help                                                   
  =======  =======================================================
  create   Create a new server or update a server secret.         
  enable   Enable a server and make it available for new meetings.
  disable  Disable a server and wait for meetings to end.         
  delete   Remove an empty server from the server list.           
  list     List all servers with their secrets.                   
  stats    Show server statistics (state, health, load).          
  =======  =======================================================

server create
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

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

server enable
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb server enable [OPTIONS] DOMAIN``

Enable a server and make it available for new meetings.

.. table:: Options
  :width: 100%

  ======  =================
  Option  Help             
  ======  =================
  DOMAIN  Required argument
  ======  =================

server disable
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb server disable [OPTIONS] DOMAIN``

Disable a server and wait for meetings to end.

.. table:: Options
  :width: 100%

  ==============  =============================================================================================
  Option          Help                                                                                         
  ==============  =============================================================================================
  DOMAIN          Required argument                                                                            
  --nuke          End all meetings on this server.                                                             
  --wait INTEGER  Wait for this many seconds for all meetings to end. A value of -1 waits forever  [default: 0]
  ==============  =============================================================================================

server delete
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb server delete [OPTIONS] DOMAIN``

Remove an empty server from the server list.

.. table:: Options
  :width: 100%

  ======  =================
  Option  Help             
  ======  =================
  DOMAIN  Required argument
  ======  =================

server list
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb server list [OPTIONS]``

List all servers with their secrets.

.. table:: Options
  :width: 100%

  ======================================  ==================================================
  Option                                  Help                                              
  ======================================  ==================================================
  --table-format [simple|plain|raw|json]  Change the result table format.  [default: simple]
  ======================================  ==================================================

server stats
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb server stats [OPTIONS]``

Show server statistics (state, health, load).

.. table:: Options
  :width: 100%

  ======================================  ==================================================
  Option                                  Help                                              
  ======================================  ==================================================
  --table-format [simple|plain|raw|json]  Change the result table format.  [default: simple]
  ======================================  ==================================================

state
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb state [OPTIONS] COMMAND [ARGS]...``

Tools to export and import cluster state in JSON files.

.. table:: Sub-Commands
  :width: 100%

  =======  =========================================================
  Command  Help                                                     
  =======  =========================================================
  export   Export current cluster state as JSON.                    
  import   Load and apply server and tenant configuration from JSON.
  =======  =========================================================

state export
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

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

state import
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

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

tenant
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb tenant [OPTIONS] COMMAND [ARGS]...``

Manage tenants

.. table:: Sub-Commands
  :width: 100%

  =======  ===============================================
  Command  Help                                           
  =======  ===============================================
  create                                                  
  enable   Enable a tenant                                
  disable  Disable a tenant                               
  list     List all tenants with their realms and secrets.
  =======  ===============================================

tenant create
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb tenant create [OPTIONS] NAME``

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

tenant enable
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb tenant enable [OPTIONS] NAME``

Enable a tenant

.. table:: Options
  :width: 100%

  ======  =================
  Option  Help             
  ======  =================
  NAME    Required argument
  ======  =================

tenant disable
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb tenant disable [OPTIONS] NAME``

Disable a tenant

.. table:: Options
  :width: 100%

  ======  ======================================
  Option  Help                                  
  ======  ======================================
  NAME    Required argument                     
  --nuke  End all meetings owned by this tenant.
  ======  ======================================

tenant list
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Usage: bbblb tenant list [OPTIONS]``

List all tenants with their realms and secrets.

.. table:: Options
  :width: 100%

  ======================================  ==================================================
  Option                                  Help                                              
  ======================================  ==================================================
  --table-format [simple|plain|raw|json]  Change the result table format.  [default: simple]
  ======================================  ==================================================

