# BBBLB Docker Compose Example

This example shows how to deploy the BBBLB application using Docker Compose.

## Prerequisites

Before we begin, ensure you have [Docker](https://docs.docker.com/engine/install/) instaleld and also the `docker-compose-plugin` package that comes with it. Do not use the legacy `docker-compose` command, but the `docker compose` plugin.

## Getting Started

1.  **Clone the Repository and copy the files you need:**  
    After cloning the repository, grab the `examples/bbblb-compose` directory with the the docker compose project files, and also the `examples/bbblb.env.example` file that lists all configuration options for BBBLB and their documentation.

    ```bash
    git clone https://github.com/defnull/bbblb.git
    cp -r bbblb/examples/bbblb-compose .
    cp bbblb/examples/bbblb.env.example bbblb-compose/bbblb.env
    # Optional: rm -r ./bbblb
    cd bbblb-compose
    ```

    You can delete the cloned repository afterwards, we do not need it.

2.  **Read, understand and customize `docker-compose.yml`**  
    The typical deployment needs three containers: `caddy` to handle SSL/TLS and serve static files, `postgres` as a database, and `bbblb` itself. Read the entire file and try to understand it. Follow the comments.

    The default settings will store any persistent data in the `./data/` host folder, so you will end up with a neat and self-contained deployment with everything in one directory.
    
    You can of cause run the http server or database outside of docker or even on a different server if you prefer, but that's usually only required for *really* large deployments or if you need redundancy. That's out of scope for a simple example.

3.  **Configure BBBLB:**  
    Open `bbblb.env` (copy of `examples/bbblb.env.example`) in an editor and change configuration as needed. Have a look for the parameters marked with `REQUIRED`.
    
    Do not change `PATH_DATA` or `DB` for now. Both are overridden in the `docker-compose.yml` file because they need to match other parts of the compose file.

4.  **Configure Caddy:**  
    Open `./caddy/Caddyfile` in an editor and change the domains Caddy should listen to. You may also want to have a look at the rest of the file and tweak it to your needs.

### Starting the Services

After you followed all the previous steps, navigate to the directory containing the `docker-compose.yml`, then run:

```bash
docker compose up --build -d
```

* `docker compose up`: Starts the services defined in `docker-compose.yml`.
*  `--build`: Builds or re-builds missing images. You can skip this step if you do not want to update caddy or bbblb. 
*  `-d`: Runs the containers in detached mode (in the background).

Since all containers are configured with `restart: unless-stopped` they will be restarted automatically after a reboot. 

### Stopping the Services

To stop all running containers, run:

```bash
docker compose stop
```

To remove the containers and networks, run:

```bash
docker compose down
```

Don't worry, all your data lives in the `./data/` directory and will not be removed by docker. You can later start everything again if you need to. 


## Managing Tenants and Servers

Most admin and maintenance tasks are easier to do with the `bbblb` admin command line tool instead of the API.
This tool must be run from within the container, though.
It needs to be able to connect to the same database and access the same configuration and storage paths as your running BBBLB service.
You can use the bundled `./bbblb.sh` script as a shortcut.

### Add your first Tenant

To create your first 'example' tenant, run:

```bash
./bbblb.sh tenant create --secret=SECRET example bbb.example.com
```

Replace `SECRET` with a suitable tenant secret, `example` with a short but meaningful tenant name, and  `bbb.example.com` with the primary domain of your BBBLB instance.

Realms are used to assign API requests to the correct tenants.
By default, this is done based on the `Host` header, which contains the hostname the tenant used to reach the API server.

In this example we associate the 'example' tenant with the primary domain. You do not have to, though. Just make sure that each tenant uses a unique domain or subdomain to reach the API server, and the domain matches their configured realm.

### Attach your first Server

> :warning: Make sure to install the `./examples/post_publish_bbblb.rb` script on BBB servers *before* attaching them to your cluster, or recordings won't be transferred.

To attach your first BBB server, run:

```bash
./bbblb.sh server create --secret=SECRET server1.example.com
```

Replace `SECRET` with the BBB server API secret and `server1.example.com` with the BBB server domain.

New servers are disabled by default. Let's enable it so it can receive new meetings:

```bash
./bbblb.sh server enable server1.example.com
```

That's it. Your 'example' tenant should now be able to start and manage meetings in your cluster via BBBLB.

