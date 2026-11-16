"""Mobility data: transit (r5py) and car (OSRM) travel times."""
import os

# r5py runs the Java routing engine R5. Java comes from the pip package jdk4py, inside the venv,
# so nothing is installed system-wide. r5py finds Java through JAVA_HOME, which we point to
# jdk4py's runtime for this process only (an existing JAVA_HOME is left untouched).
try:
    import jdk4py

    os.environ.setdefault("JAVA_HOME", str(jdk4py.JAVA_HOME))
except ImportError:  # jdk4py not installed: r5py will look for a system Java instead
    pass
