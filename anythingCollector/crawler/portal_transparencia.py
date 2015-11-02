from . import Crawler
from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from collections import defaultdict
import re


class CrawlerPortalTransparencia(Crawler):
    def create_my_table(self):
        self.db.execute('CREATE TABLE IF NOT EXISTS %s('
                            'peopleid INTEGER,'
                            'federal_employee_type TEXT,'
                            'job TEXT,'
                            'workplace TEXT,'
                            'working_hours TEXT,'
                            'FOREIGN KEY(peopleid) REFERENCES peoples(id)'
                        ');' % self.name())

        self.db.execute('CREATE TABLE IF NOT EXISTS %s('
                            'peopleid INTEGER,'
                            'remunerationid INTEGER PRIMARY KEY AUTOINCREMENT,'
                            'month INTEGER,'
                            'year INTEGER,'
                            'FOREIGN KEY(peopleid) REFERENCES peoples(id)'
                        ');' % (self.name() + '_remuneration_date'))

        self.db.execute('CREATE TABLE IF NOT EXISTS %s('
                            'peopleid INTEGER,'
                            'remunerationid INTEGER,'
                            'type TEXT,'
                            'value INTEGER,'
                            'FOREIGN KEY(peopleid) REFERENCES peoples(id),'
                            'FOREIGN KEY(remunerationid) REFERENCES %s(remunerationid)'
                        ');' % (self.name() + '_remuneration_info', self.name() + '_remuneration_date'))

    @staticmethod
    def name():
        return 'portal_transparencia'

    @staticmethod
    def dependencies():
        return ('cpf',), ('name',),

    @staticmethod
    def crop():
        # todo: retorna o cpf, porém, parcial. Preciso aumentar o grau de abstração para dizer que está colhendo parte do cpf
        return '',

    @classmethod
    def harvest(cls, id=None, dependencies=None, specific_name=None, specific_siteid=None):
        phantom = webdriver.PhantomJS()

        # retorna o siteid da(s) pessoa(s) com a query específica
        # a query pode ser tanto o nome (parcial ou não), o cpf (apenas completo) ou None
        # lembrando que se usar o nome "José da Silva" retornará todos que tenham "José da Silva" no nome
        def get_siteid_specific(query):
            phantom.get('http://www.portaltransparencia.gov.br/servidores/Servidor-ListaServidores.asp')

            if query is not None:
                phantom.find_element_by_id('Pesquisa').send_keys(query)
                phantom.find_element_by_css_selector('#pesquisaListagem [type="submit"]').click()

                text = phantom.execute_script('return $(\'[summary="Lista de Servidores"] td:first\').text()')
                if text[:31] == 'Não foram encontrados registros':
                    return []

            regexp_total_pages = re.compile(r'(\d+)$')
            total_pages = int(regexp_total_pages.search(
                phantom.find_element_by_class_name('paginaAtual').get_attribute('innerHTML')
            ).group(1))

            list_to_return = []

            for i in range(0, total_pages):
                peoples = phantom.find_elements_by_css_selector('[summary="Lista de Servidores"] tr:not(:first-child)')
                for i2 in peoples:
                    list_to_return.append({'siteid': i2.find_element_by_tag_name('a').get_attribute('href')[-7:],
                                           'name': i2.find_element_by_tag_name('a').get_attribute('innerHTML').strip().title()})

                if i + 1 != total_pages:
                    if i == 0:
                        element_next_page = phantom.find_elements_by_css_selector('#paginacao a')[0]
                    else:
                        element_next_page = phantom.find_elements_by_css_selector('#paginacao a')[2]

                    element_next_page.click()

            return list_to_return

        # listar todos os siteids de servidores
        def get_siteid_all():
            return get_siteid_specific(None)

        # se fornecer o id no banco de dados da pessoa, vai puxar depedencia e tentar buscar pelo CPF ou Nome e então coletar os dados
        # se nao fornecer id, não vai puxar depedencias e vai coletar o Portal da Transparencia inteiro
        if (specific_name is not None) and (specific_siteid is not None):
            raise ValueError("Só forneça um dos dois: ou o nome a ser buscando ou o siteid")

        if (id is not None) and ((specific_name is not None) or (specific_siteid is not None)):
            raise ValueError("Não forneça o id no banco de dados junto do nome a ser buscado ou o siteid")

        if id is None:
            if (specific_name is None) and (specific_siteid is None):
                # Se nada for especificado, recolherá todo o site
                list_peoples = get_siteid_all()
            elif specific_name is not None:
                # Se tiver sido fornecido o nome a ser buscado, usará na busca
                list_peoples = get_siteid_specific(specific_name)
            elif specific_siteid is not None:
                # Se tiver sido fornecido o id no site, checará apenas ele
                # todo: talvez seja bom verificar se o siteid é válido
                list_peoples = [specific_siteid]
            else:
                raise ValueError("Condição não especificada antes")
        else:
            # Se for especificado o id da pessoa na tabela, usará as informações já coletadas no decorator GetDependencies
            if 'name' in dependencies:
                query = dependencies['name']
            else:
                query = dependencies['cpf']
            list_peoples = get_siteid_specific(query)

            # Nós queremos apenas a pessoa da query especificada, e mais nenhuma além
            # Em alguns casos, se pesquisar pelo nome, pode vim outras pessoas da qual não estamos interessados, como, por exemplo,
            # se pesquisar por "Francisco José da Silva", pode vim "Francisco José da Costa e Silva", mas não queremos este último
            if 'name' in dependencies:
                list_peoples = [{'siteid': i['siteid'], 'name': i['name']} for i in list_peoples
                                if i['name'] == dependencies['name']]

            # Se tiver retornado nada, é porque essa pessoa especificada não está presente no Portal da Transparencia
            if len(list_peoples) == 0:
                cls.update_crawler(id, -1)
                return

        list_peoples = [i['siteid'] for i in list_peoples]
        for current_siteid in list_peoples:
            phantom.get('http://www.portaltransparencia.gov.br/servidores/Servidor-DetalhaServidor.asp?IdServidor=' + str(current_siteid))

            # Infos básicas
            people_name, people_cpf, people_federal_employee_type =\
                [i.get_attribute('innerHTML').strip() for i in phantom.execute_script('return $(\'[summary="Identificação do Servidor"] tr:not(:first) .colunaValor\')')]

            people_name = people_name.title()

            cls.db.update_people({'name': people_name}) # todo: precisa lidar com o caso do cpf ser parcial
            #cls.db.update_people({'name': people_name}, {'cpf': people_cpf})
            tableid = cls.db.get_tableid_of_people({'name': people_name})

            # Infos do emprego
            jobs_infos = phantom.execute_script(
                """
                  // Nessa hash fica a "tradução" dos dados das colunas da variável want,
                  // do nome da coluna presente no Portal da Transparência com o nome da coluna a ser salva no banco de dados do crawler
                  var translate = {'Cargo Emprego': 'job', 'Órgão': 'workplace', 'Jornada de Trabalho': 'working_hours'};

                  function get_infos(panel_target) {
                    // Deve-se definir nessa array os dados que deseja obter
                    // Caso o dado desejado seja um sub-item, deve-se usar array com os nomes dos itens até chegar a ele
                    var want = ['Cargo Emprego', ['Local de Exercício - Localização', 'Órgão'], 'Jornada de Trabalho'];

                    // Retorna se esse a coluna dada tem um dado desejado ou não
                    // Lida com os dados desejado em sub-itens
                    function check_if_want_this_columns(column_key) {
                      for (var i in want) {
                        if (typeof(want[i]) == 'string') {
                          if (column_key == want[i]) {
                            want.splice(i, 1);
                            return true
                          }
                        } else if (typeof(want[i]) == 'object') {
                          if (column_key == want[i][0]) {
                            want[i].splice(0, 1);
                            if (want[i].length == 1) {
                              want[i] = want[i][0];
                              return false
                            }
                          }
                        }
                      }

                      return false
                    }

                    // Fazer parser em cada linha da tabela de informações da pessoa e vai armazenando o resultado na hash to_return
                    var to_return = {};
                    panel_target.find('tr:not(:first)').each(function (k, v) {
                      var current_column = $(v).find('td').eq(0).html().replace(/&nbsp;/gi, '').trim();
                      if (current_column.slice(-1) == ':') {
                        current_column = current_column.slice(0, -1);
                      }

                      if (check_if_want_this_columns(current_column)) {
                        to_return[translate[current_column]] = $(v).find('td')[1].innerText;
                      }
                    });

                    return to_return;
                  }

                  // Pegar os paneis de emprego.
                  // Cuidado: há casos em que existe um painel vazio que serve de divisão entre um emprego e outro
                  var panels_empregos = $('#listagemConvenios tr:first > td').filter( function(k, v) { return (v.innerHTML != '') } );

                  var infos_crop = [];

                  var i;
                  for (i = 0; i < panels_empregos.length; i++) {
                    infos_crop.push(get_infos($(panels_empregos[i])))
                  }

                  //console.log(infos_crop);
                  return infos_crop;
                """
            )

            if jobs_infos is not None:
                for i in jobs_infos:
                    i = defaultdict(lambda: None, i)
                    cls.update_my_table(tableid,
                                        {'federal_employee_type': people_federal_employee_type,
                                         'job': i['job'], 'workplace': i['workplace'], 'working_hours': i['working_hours']})

            # Infos salariais
            phantom.get('http://www.portaltransparencia.gov.br/servidores/Servidor-DetalhaRemuneracao.asp?Op=1&bInformacaoFinanceira=True&IdServidor=' + str(current_siteid))

            regexp_date = re.compile(r'(\w+) de (\d+)$')
            total_dates = len(phantom.find_elements_by_css_selector('#navegacaomeses a'))
            for i in range(total_dates):
                phantom.find_elements_by_css_selector('#navegacaomeses a')[i].click()

                if phantom.execute_script("return $('td[colspan=5]').text() == \"Informação não disponibilizada \""):
                    continue

                month, year =\
                    regexp_date.search(
                        phantom.find_element_by_css_selector('.remuneracaohead1 th').get_attribute('innerHTML')
                    ).groups()

                cls.update_my_table(tableid, {'month': month, 'year': year}, table='remuneration_date')
                remunerationid = cls.db.lastrowid()

                remunerations_infos = phantom.execute_script(
                    """
                      var to_return = [];
                      $('td[colspan=2]').each(function(i) {
                        var to_push = {};
                        to_push['type'] = $(this).text();
                        to_push['value'] = $(this).parent().find('.colunaValor').text().replace('.','').replace(',','.');
                        to_return.push(to_push);
                      });
                      return to_return;
                    """
                )

                for i2 in remunerations_infos:
                    cls.update_my_table(tableid, {'remunerationid': remunerationid, 'type': i2['type'], 'value': i2['value']}, table='remuneration_info')

            # Finalizar
            cls.update_crawler(people_name, 1)
