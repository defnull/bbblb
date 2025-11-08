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

## Starting the Services

After you followed all the previous steps, navigate to the directory containing the `docker-compose.yml`, then run:

```bash
docker compose up --build -d
```

* `docker compose up`: Starts the services defined in `docker-compose.yml`.
*  `--build`: Builds or re-builds missing images. You can skip this step if you do not want to update caddy or bbblb. 
*  `-d`: Runs the containers in detached mode (in the background).

Since all containers are configured with `restart: unless-stopped` they will be restarted automatically after a reboot. 

## Stopping the Services

To stop all running containers, run:

```bash
docker compose stop
```

To remove the containers and networks, run:

```bash
docker compose down
```

Don't worry, all your data lives in the `./data/` directory and will not be removed by docker. You can later start everything again if you need to. 
