ARG WATER_BASE_IMAGE
FROM ${WATER_BASE_IMAGE}

# Keep the deployed compatibility adapters and business code intact.
COPY --from=zhixun_src /shared/mcp_auth.py /opt/water-auth/mcp_auth.py
COPY authenticated_entrypoint.py /opt/water-auth/authenticated_entrypoint.py
CMD ["python", "/opt/water-auth/authenticated_entrypoint.py", "sse", "--host", "0.0.0.0", "--port", "18201"]
