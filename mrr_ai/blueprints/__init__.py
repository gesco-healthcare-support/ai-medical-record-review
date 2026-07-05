"""Blueprint registration."""


def register_blueprints(app):
    from mrr_ai.blueprints.export import bp as export_bp
    from mrr_ai.blueprints.extraction import bp as extraction_bp
    from mrr_ai.blueprints.individual_mrr import bp as individual_bp
    from mrr_ai.blueprints.pages import bp as pages_bp
    from mrr_ai.blueprints.reports import bp as reports_bp
    from mrr_ai.blueprints.review_api import bp as review_api_bp
    from mrr_ai.blueprints.segmentation import bp as segmentation_bp
    from mrr_ai.blueprints.summarize import bp as summarize_bp
    from mrr_ai.blueprints.upload import bp as upload_bp

    for bp in (
        pages_bp,
        upload_bp,
        summarize_bp,
        reports_bp,
        export_bp,
        extraction_bp,
        individual_bp,
        segmentation_bp,
        review_api_bp,
    ):
        app.register_blueprint(bp)
