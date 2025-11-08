
## Stage 1) Build bbb-playback sow e can bundle it.
## We can skip all this once the upstream project provides docker images
FROM node:22-alpine AS playback-build

WORKDIR /build
RUN apk add --no-cache git
ENV SOURCE_GIT=https://github.com/bigbluebutton/bbb-playback.git
RUN LATEST_TAG=$(\
    git ls-remote --tags --sort="-v:refname" $SOURCE_GIT "refs/tags/v*^{}"\
    | sed -E 's|.*(v[0-9]+\.[0-9]+\.[0-9]+-?)\^.*|\1|'\
    | fgrep -v '-' | head -n1) \
    git clone --tag $LATEST_TAG --depth 1 $SOURCE_GIT .

ENV REACT_APP_MEDIA_ROOT_URL=/playback/presentation
ENV REACT_APP_MEDIA_ROOT_URL=/playback/presentation
RUN npm ci
RUN npm run-script build
RUN find .



## Stage 2: Build caddy. This can be skipped if you do not need any custom
## modules.
#FROM caddy:builder AS caddy-build
#RUN xcaddy build \
#    --with github.com/caddy-dns/rfc2136 \
#    --with github.com/hairyhenderson/caddy-teapot-module@v0.0.3-0



## Stage 3: Just a standard caddy image with bundled bbb-playback files
FROM caddy:alpine 

## Copy modified caddy binary, if you have built one in Stage 2)
#COPY --from=caddy-build /usr/bin/caddy /usr/bin/caddy

## Bundle the player files from Stage 1)
COPY --from=playback-build /build/build /usr/share/bbb-playback/

