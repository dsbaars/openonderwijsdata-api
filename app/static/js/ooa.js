'use strict';

// Declare app level module which depends on views, and components
angular.module('myApp', ['ui.router'], function($stateProvider, $urlRouterProvider, $locationProvider) {
    $urlRouterProvider.otherwise("/");
    $locationProvider.html5Mode(true);
});

angular.module('myApp').config(function($stateProvider, $urlRouterProvider) {

    $stateProvider
       .state('index', {
         url: "/",
         templateUrl: "static/home.html",
         controller: 'HomeCtrl'
       })
       .state('stats', {
         url: "/stats",
         templateUrl: "static/stats.html"
     })
       .state('search', {
         url: "/search?q",
         templateUrl: "static/search.html",
         controller: 'SearchCtrl'
     });

});

angular.module('myApp').controller('HomeCtrl', ['$scope', '$http', '$state', function($scope, $http, $state) {

    $scope.onFormSubmit = function () {
        $state.go('search', {q: $scope.query});
    }

}]).controller('SearchCtrl', ['$scope', '$http', '$stateParams', '$state', function($scope, $http, $stateParams, $state) {
    $scope.query = $stateParams.q;

    $http({method: 'GET', url: 'http://localhost:5001/api/v1/search', params: { q: $stateParams.q }}).
        success(function(data,status,headers,config) {
            $scope.results = data.hits;
            $scope.took = data.took;
            $scope.total = data.total;
        });

    $scope.onFormSubmit = function () {
        $state.go('search', {q: $scope.query});
    }
}]);
