"""Coordinate conversion from the Sims 2 PSP Z-up basis to glTF Y-up."""

import math

import numpy as np

# Row-vector basis change: (x, y, z) -> (-x, z, y).
# This maps PSP Z-up to glTF Y-up and turns the character 180 degrees around Y.
BASIS_ROW = np.array(
    [
        [-1.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
)


def convert_vector3(vector):
    x, y, z = vector
    return -x, z, y


def convert_row_matrix(matrix):
    """Conjugate a source row-vector transform into the glTF coordinate basis."""
    matrix = np.asarray(matrix, dtype=np.float64)
    if matrix.shape != (4, 4):
        raise ValueError(f"Invalid transform matrix: {matrix.shape}")
    return BASIS_ROW.T @ matrix @ BASIS_ROW


def convert_quaternion(quaternion):
    """Conjugate a rotation quaternion into the glTF coordinate basis."""
    half_angle = math.sqrt(0.5)
    change = (0.0, half_angle, half_angle, 0.0)
    inverse = (0.0, -half_angle, -half_angle, 0.0)
    x, y, z, w = quaternion

    def multiply(a, b):
        ax, ay, az, aw = a
        bx, by, bz, bw = b
        return (
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz,
        )

    return multiply(multiply(change, (x, y, z, w)), inverse)
