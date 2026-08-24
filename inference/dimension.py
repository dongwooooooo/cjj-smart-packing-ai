"""Dimension estimation logic.

This module is a placeholder. The real implementation lives in the
private Hugging Face repository `ek09/logistics-dimension-3view` and
will be copied here (with a diff review) once an access token is
available. Keep the function signature below stable so `server.py`
does not need to change when the real code lands.
"""

from typing import Any


def estimate_dimensions(images: list[Any]) -> dict[str, Any]:
    """Estimate package dimensions from three view images.

    Args:
        images: Three images (front/side/top or equivalent views),
            already decoded into whatever format the real model
            expects (e.g. PIL.Image or numpy.ndarray).

    Returns:
        A dict describing the estimated dimensions, e.g.
        {"length_cm": ..., "width_cm": ..., "height_cm": ..., "confidence": ...}.

    Raises:
        NotImplementedError: Always, in this skeleton. Replace this
            function body with the implementation from HF
            ek09/logistics-dimension-3view/dimension.py.
    """
    raise NotImplementedError(
        "estimate_dimensions is not implemented yet. "
        "Replace this with the real logic from HF ek09/logistics-dimension-3view "
        "once an access token is available."
    )
