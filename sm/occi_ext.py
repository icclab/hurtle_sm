__author__ = 'florian'
import occi.core_model
COST_TEMPLATE = occi.core_model.Mixin('http://schemas.ogf.org/occi/platform#',
                                      'cost_tpl')

LOCATION_TEMPLATE = occi.core_model.Mixin('http://schemas.ogf.org/occi/platform#',
                                      'location_tpl')

class CostTemplate(occi.core_model.Mixin):
    """
    Application type template.
    """

    pass


class LocationTemplate(occi.core_model.Mixin):
    """
    Application type template.
    """

    pass